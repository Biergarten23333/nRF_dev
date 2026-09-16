#!/usr/bin/env python3
"""Fresh-process resource qualification for the offline U6 adapter."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import resource
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
U5A = ROOT / "logs/c2_tight_range_u5a_revision_002_20260906T143413Z"
U5A_EXPECTED_SEAL = "1b12acef002da1bb720c8777abc248db4820e0edd2c2f203d4c84bf460494eff"
PRIOR_BLOCKED = ROOT / "logs/c2_offline_uncertainty_u6_20260906T145520Z"
PRIOR_BLOCKED_EXPECTED_SEAL = "e8b3f04d423ae3fd44ea1dd6c5a4bacf92959c59119b77ac5d599497187c8a68"
OWNED = (
    ROOT / "src/biospur_fusion/c2_uwb_root_world/tight_range.py",
    ROOT / "src/biospur_fusion/c2_uwb_root_world/offline_uncertainty_adapter.py",
    ROOT / "tests/test_c2_tight_raw_range.py",
    ROOT / "tests/test_c2_tight_range_linearization.py",
    ROOT / "tests/test_c2_offline_uncertainty_adapter.py",
    Path(__file__).resolve(),
)
TEST_PATHS = (
    "tests/test_c2_tight_raw_range.py",
    "tests/test_c2_tight_range_linearization.py",
    "tests/test_c2_offline_uncertainty_adapter.py",
)
THREAD_ENV = {
    "OMP_NUM_THREADS": "1",
    "OPENBLAS_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1",
}
RSS_CAP_KIB = 300_000
MARGINAL_HWM_CAP_KIB = 20_000
EVIDENCE_CAP_BYTES = 20_000_000


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _verify_seal(directory: Path, expected_digest: str) -> dict[str, object]:
    seal = directory / "SHA256SUMS"
    if _sha(seal) != expected_digest:
        raise RuntimeError(f"seal digest mismatch: {directory}")
    verified = 0
    for line in seal.read_text().splitlines():
        digest, name = line.split("  ", 1)
        member = directory / name
        if not member.is_file() or _sha(member) != digest:
            raise RuntimeError(f"sealed member mismatch: {directory}/{name}")
        verified += 1
    return {
        "path": str(directory.relative_to(ROOT)),
        "seal_sha256": expected_digest,
        "verified_members": verified,
    }


def _proc_memory() -> dict[str, int]:
    values: dict[str, int] = {}
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith(("VmRSS:", "VmHWM:")):
            name, value, unit = line.split()
            if unit != "kB":
                raise RuntimeError("unexpected /proc memory unit")
            values[name.rstrip(":") + "_kib"] = int(value)
    if set(values) != {"VmRSS_kib", "VmHWM_kib"}:
        raise RuntimeError("missing /proc memory owner")
    return values


def _usage(who: int) -> dict[str, float | int]:
    usage = resource.getrusage(who)
    return {
        "ru_maxrss_kib": int(usage.ru_maxrss),
        "ru_utime_s": float(usage.ru_utime),
        "ru_stime_s": float(usage.ru_stime),
    }


def _benchmark_child(telemetry_path: Path) -> int:
    if any(os.environ.get(name) != value for name, value in THREAD_ENV.items()):
        raise SystemExit("thread environment must be frozen before NumPy import")
    import numpy as np

    from biospur_fusion.c2_uwb_root_world.offline_uncertainty_adapter import (
        UNAVAILABLE,
        adapt_raw_range_uncertainty,
    )
    from biospur_fusion.c2_uwb_root_world.tight_range import (
        ExternalRangeInformationWeights,
        RangeBiasPriorSnapshot,
        linearize_raw_range_factors,
    )
    from biospur_fusion.c2_uwb_root_world.u0 import ClockModel, UwbRow
    from biospur_fusion.root_r3.models import RootState

    memory = {"post_import": _proc_memory()}
    anchors = np.array([
        [0, 0, 0], [4, 0, 0], [4, 3, 0], [0, 3, 0],
        [0, 0, 2], [4, 0, 2], [4, 3, 2], [0, 3, 2],
    ], dtype=float)
    truth = np.array([2.0, 1.2, 0.9])
    ranges = np.linalg.norm(anchors - truth, axis=1); ranges[3] += 0.4
    row = UwbRow(
        "BSFC2CC", 0, 1, 1, 1_000_000, 1_010_000, tuple(range(8)),
        tuple(int(round(value * 1000)) for value in ranges),
        tuple(1000 + 500 * index for index in range(8)), (100,) * 8, 0xFF,
    )
    clock = ClockModel(0, 1000.0, 0.0, 0.0)
    epochs = np.array([
        clock.seconds(row.strobe_us + 0.5 * value) for value in row.t_round_us
    ])
    vector = np.zeros(9); vector[:3] = truth; vector[3:6] = [0.2, -0.1, 0.05]
    state = RootState(float(np.median(epochs)), vector, np.eye(9) * 0.04)
    prior = RangeBiasPriorSnapshot(
        row.node, float(epochs.min() - 0.01), np.zeros(8), np.full(8, 0.01),
        np.full(8, np.nan),
    )
    weights = ExternalRangeInformationWeights(
        row.node, prior.snapshot_time_s, np.linspace(0.25, 1.0, 8), "U6_FIXTURE")
    factor = linearize_raw_range_factors(
        state, row, anchors_m=anchors, clock=clock, bias_prior=prior,
        information_weights=weights,
    )
    memory["post_fixture"] = _proc_memory()
    elapsed_ms = np.empty(20_000)
    for index in range(elapsed_ms.size):
        started = time.perf_counter_ns()
        audit = adapt_raw_range_uncertainty(factor, state)
        elapsed_ms[index] = (time.perf_counter_ns() - started) * 1e-6
    memory["post_20000"] = _proc_memory()
    if audit.cross_covariance_status != UNAVAILABLE or audit.deleted_link_count != 0:
        raise RuntimeError("offline adapter boundary changed during benchmark")
    marginal = memory["post_20000"]["VmHWM_kib"] - memory["post_fixture"]["VmHWM_kib"]
    telemetry = {
        "thread_environment": {name: os.environ.get(name) for name in THREAD_ENV},
        "calls": int(elapsed_ms.size),
        "p50_ms": float(np.quantile(elapsed_ms, 0.50)),
        "p99_ms": float(np.quantile(elapsed_ms, 0.99)),
        "maximum_ms": float(np.max(elapsed_ms)),
        "fixed_link_count": audit.structurally_valid_link_count,
        "memory_checkpoints": memory,
        "adapter_marginal_vmhwm_kib": int(marginal),
        "rusage_self": _usage(resource.RUSAGE_SELF),
        "rusage_children": _usage(resource.RUSAGE_CHILDREN),
    }
    _write_json(telemetry_path, telemetry)
    return 0


def _external_maxrss(time_path: Path) -> int:
    for line in time_path.read_text().splitlines():
        if "Maximum resident set size (kbytes):" in line:
            return int(line.rsplit(":", 1)[1].strip())
    raise RuntimeError(f"missing external RSS: {time_path}")


def _run_timed(command: list[str], output: Path, stem: str, env: dict[str, str]) -> dict[str, object]:
    time_path = output / f"{stem}_TIME.txt"
    stdout_path = output / f"{stem}_STDOUT.txt"
    stderr_path = output / f"{stem}_STDERR.txt"
    timed = ["/usr/bin/time", "-v", "-o", str(time_path), *command]
    started = time.perf_counter()
    with stdout_path.open("w") as stdout, stderr_path.open("w") as stderr:
        completed = subprocess.run(
            timed, cwd=ROOT, env=env, stdout=stdout, stderr=stderr,
            timeout=120, check=False,
        )
    return {
        "command": command,
        "returncode": completed.returncode,
        "wall_s": time.perf_counter() - started,
        "external_maximum_rss_kib": _external_maxrss(time_path),
        "time_file": time_path.name,
        "stdout_file": stdout_path.name,
        "stderr_file": stderr_path.name,
    }


def _read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text())


def _orchestrate(output: Path, literal_command: str) -> int:
    if output.exists():
        raise SystemExit("output must not exist")
    started = time.perf_counter()
    u5a = _verify_seal(U5A, U5A_EXPECTED_SEAL)
    prior = _verify_seal(PRIOR_BLOCKED, PRIOR_BLOCKED_EXPECTED_SEAL)
    before = {str(path.relative_to(ROOT)): _sha(path) for path in OWNED}
    output.mkdir(parents=True)
    env = dict(os.environ); env.update(THREAD_ENV); env["PYTHONPATH"] = "src:tools:."
    test_command = [sys.executable, "-m", "pytest", "-q", *TEST_PATHS]
    test = _run_timed(test_command, output, "TEST_CHILD", env)
    telemetry_path = output / "BENCHMARK_TELEMETRY.json"
    benchmark_command = [
        sys.executable, str(Path(__file__).resolve()),
        "--benchmark-child", "--telemetry", str(telemetry_path),
    ]
    benchmark_process = (
        _run_timed(benchmark_command, output, "BENCHMARK_CHILD", env)
        if test["returncode"] == 0 else {"returncode": "NOT_RUN_TEST_FAILURE"}
    )
    benchmark = _read_json(telemetry_path) if telemetry_path.is_file() else {}
    after = {str(path.relative_to(ROOT)): _sha(path) for path in OWNED}
    parent_memory = _proc_memory()
    parent_self = _usage(resource.RUSAGE_SELF)
    parent_children = _usage(resource.RUSAGE_CHILDREN)
    preliminary_bytes = sum(path.stat().st_size for path in output.iterdir())
    passed = bool(
        test["returncode"] == 0
        and benchmark_process["returncode"] == 0
        and before == after
        and test["external_maximum_rss_kib"] < RSS_CAP_KIB
        and benchmark_process["external_maximum_rss_kib"] < RSS_CAP_KIB
        and parent_memory["VmHWM_kib"] < RSS_CAP_KIB
        and benchmark.get("rusage_self", {}).get("ru_maxrss_kib", math.inf) < RSS_CAP_KIB
        and benchmark.get("adapter_marginal_vmhwm_kib", math.inf) <= MARGINAL_HWM_CAP_KIB
        and benchmark.get("calls") == 20_000
        and preliminary_bytes < EVIDENCE_CAP_BYTES
    )
    result = {
        "schema": "biospur.c2.offline_uncertainty.u6.fixture.resource.v2",
        "status": "READY_FOR_MONITOR_U6_FIXTURE_REVIEW" if passed else "BLOCKED_U6_FIXTURE_RESOURCE",
        "execution_class": "OFFLINE_FIXTURE_ONLY",
        "scientific_pass": False,
        "calibrated_R": False,
        "production_ready": False,
        "real_data_opened": False,
        "raw_or_hxx_opened": False,
        "action04_opened": False,
        "optional_action04_coverage_pass": "NOT_RUN_FIXTURE_ONLY",
        "prior_blocked_evidence": {**prior, "promotion_status": "NON_PROMOTED"},
        "u5a_revision_002": u5a,
        "source_hashes_unchanged": before == after,
        "cross_covariance_default": "UNAVAILABLE_NOT_PROPAGATED",
        "joint_covariance_claim_without_full_augmented_owner": "FAIL_CLOSED",
        "zero_structurally_valid_link_deletion": True,
        "resource_caps": {
            "parent_and_children_rss_kib_strictly_less_than": RSS_CAP_KIB,
            "adapter_marginal_vmhwm_kib_at_most": MARGINAL_HWM_CAP_KIB,
            "evidence_bytes_strictly_less_than": EVIDENCE_CAP_BYTES,
        },
        "test_child": test,
        "benchmark_child": benchmark_process,
        "benchmark": benchmark,
        "parent_memory": parent_memory,
        "parent_rusage_self": parent_self,
        "parent_rusage_children": parent_children,
        "wall_s": time.perf_counter() - started,
    }
    _write_json(output / "RESULT.json", result)
    _write_json(output / "HASHES.json", {"before": before, "after": after})
    (output / "COMMAND.txt").write_text(literal_command.rstrip() + "\n")
    (output / "REPORT.md").write_text(
        "# U6 offline uncertainty resource qualification\n\n"
        f"Status: `{result['status']}`. Tests returned {test['returncode']} with "
        f"external peak RSS {test.get('external_maximum_rss_kib')} KiB. The exact "
        f"20,000-call benchmark returned {benchmark_process['returncode']} with external "
        f"peak RSS {benchmark_process.get('external_maximum_rss_kib')} KiB and adapter "
        f"marginal VmHWM {benchmark.get('adapter_marginal_vmhwm_kib')} KiB.\n\n"
        "The earlier RSS-blocked seal is bound as non-promoted. This resource revision "
        "does not change adapter algebra, tests, tolerances, or production owners. No "
        "raw data, HXX, or action04 capture was opened. Scientific, calibrated-R, and "
        "production claims remain false.\n"
    )
    members = sorted(path for path in output.iterdir() if path.name != "SHA256SUMS")
    (output / "SHA256SUMS").write_text("".join(
        f"{_sha(path)}  {path.name}\n" for path in members))
    if sum(path.stat().st_size for path in output.iterdir()) >= EVIDENCE_CAP_BYTES:
        raise SystemExit("sealed evidence exceeds 20 MB")
    return 0 if passed else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--benchmark-child", action="store_true")
    parser.add_argument("--telemetry", type=Path)
    args = parser.parse_args()
    if args.benchmark_child:
        if args.output is not None or args.telemetry is None:
            raise SystemExit("benchmark child requires only --telemetry")
        return _benchmark_child(args.telemetry)
    if args.output is None or args.telemetry is not None:
        raise SystemExit("orchestrator requires only --output")
    literal = (
        "PYTHONPATH=src:tools:. .venv-v0/bin/python "
        "tools/preflight_c2_offline_uncertainty_u6.py "
        f"--output {args.output}"
    )
    return _orchestrate(args.output.resolve(), literal)


if __name__ == "__main__":
    raise SystemExit(main())
