#!/usr/bin/env python3
"""One observation-only U6 replay of the sealed U5B action04 prefix."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import resource
import subprocess
import sys
import time
import traceback


ROOT = Path(__file__).resolve().parents[1]
U5B = ROOT / "logs/c2_tight_range_u5b_revision_002_action04_first5s_20260906T144646Z"
U5B_SEAL = "cb0da6312eefb1d249d216c1560282d1fa221f4132fd9a9f2fa64e56ec5a5574"
U6_FIXTURE = ROOT / "logs/c2_offline_uncertainty_u6_resource_revision_20260906T150150Z"
U6_FIXTURE_SEAL = "7fea7b21516f324fc5bf05cc45601ef13ad1a02ae6c49d0b5d74318183615d5d"
RSS_CAP_KIB = 300_000
EVIDENCE_CAP_BYTES = 50_000_000
EXPECTED_GROUPS = 41
EXPECTED_SWEEPS = 410
EXPECTED_LINKS = 3266


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def _verify_seal(directory: Path, expected: str) -> dict[str, object]:
    seal = directory / "SHA256SUMS"
    if _sha(seal) != expected:
        raise RuntimeError(f"seal mismatch: {directory}")
    count = 0
    for line in seal.read_text().splitlines():
        digest, name = line.split("  ", 1)
        if _sha(directory / name) != digest:
            raise RuntimeError(f"sealed member mismatch: {directory / name}")
        count += 1
    return {"path": str(directory.relative_to(ROOT)), "seal_sha256": expected,
            "verified_members": count}


def _proc_memory() -> dict[str, int]:
    result: dict[str, int] = {}
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith(("VmRSS:", "VmHWM:")):
            name, value, unit = line.split()
            if unit != "kB":
                raise RuntimeError("unexpected memory unit")
            result[name.rstrip(":") + "_kib"] = int(value)
    return result


def _external_rss(path: Path) -> int:
    for line in path.read_text().splitlines():
        if "Maximum resident set size (kbytes):" in line:
            return int(line.rsplit(":", 1)[1])
    raise RuntimeError("external maximum RSS unavailable")


def _child(replay_output: Path, audit_output: Path) -> int:
    import numpy as np

    from biospur_fusion.c2_uwb_root_world.offline_uncertainty_adapter import (
        SUPPLIED,
        UNAVAILABLE,
        adapt_raw_range_uncertainty,
    )
    import run_c2_tight_range_u5b as u5b

    original_linearize = u5b.linearize_raw_range_factors
    counts = Counter()
    maximum_errors = Counter()

    def observed_linearize(state, row, **kwargs):
        factor = original_linearize(state, row, **kwargs)
        audit = adapt_raw_range_uncertainty(factor, state)
        counts["sweeps"] += 1
        counts["links"] += len(factor.anchors)
        counts["sensor_r_finite_symmetric_psd"] += 1
        counts["total_prior_r_finite_symmetric_psd"] += 1
        counts["robust_effective_r_finite_symmetric_psd"] += 1
        counts["prior_s_finite_symmetric_psd"] += 1
        counts["effective_s_finite_symmetric_psd"] += 1
        counts["zero_valid_link_deletion"] += int(audit.deleted_link_count == 0)
        counts["cross_covariance_unavailable"] += int(
            audit.cross_covariance_status == UNAVAILABLE)
        counts["cross_covariance_supplied"] += int(
            audit.cross_covariance_status == SUPPLIED)
        maximum_errors["prior_nis"] = max(
            maximum_errors["prior_nis"], abs(audit.prior_nis - factor.prior_nis))
        maximum_errors["rank"] = max(
            maximum_errors["rank"], abs(audit.rank - factor.rank))
        maximum_errors["condition"] = max(
            maximum_errors["condition"], abs(audit.condition - factor.condition))
        expected_effective_s = (
            factor.state_jacobian @ state.covariance @ factor.state_jacobian.T
            + np.diag(np.diag(factor.r_prior_m2) / factor.robust_weights)
        )
        expected_effective_nis = float(
            factor.innovations_m
            @ np.linalg.solve(expected_effective_s, factor.innovations_m))
        maximum_errors["effective_s"] = max(
            maximum_errors["effective_s"],
            float(np.max(np.abs(audit.robust_effective_s_m2 - expected_effective_s))))
        maximum_errors["effective_nis"] = max(
            maximum_errors["effective_nis"],
            abs(audit.robust_effective_nis - expected_effective_nis))
        return factor

    u5b.linearize_raw_range_factors = observed_linearize
    prior_argv = sys.argv
    sys.argv = ["run_c2_tight_range_u5b.py", "--output", str(replay_output)]
    started = time.perf_counter()
    try:
        returncode = u5b.main()
    finally:
        sys.argv = prior_argv
        u5b.linearize_raw_range_factors = original_linearize
    _write_json(audit_output, {
        "returncode": returncode,
        "coverage": dict(sorted(counts.items())),
        "maximum_absolute_recomputation_errors": dict(sorted(maximum_errors.items())),
        "wall_s": time.perf_counter() - started,
        "rusage_self_maxrss_kib": int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss),
        "memory": _proc_memory(),
        "adapter_observation_only": True,
    })
    return returncode


def _seal(output: Path) -> str:
    members = sorted(
        path for path in output.rglob("*")
        if path.is_file() and path != output / "SHA256SUMS"
    )
    (output / "SHA256SUMS").write_text("".join(
        f"{_sha(path)}  {path.relative_to(output)}\n" for path in members))
    return _sha(output / "SHA256SUMS")


def _parent(output: Path, literal_command: str) -> int:
    if output.exists():
        raise FileExistsError(output)
    started = time.perf_counter()
    u5b_binding = _verify_seal(U5B, U5B_SEAL)
    u6_binding = _verify_seal(U6_FIXTURE, U6_FIXTURE_SEAL)
    reference_result = json.loads((U5B / "RESULT.json").read_text())
    raw_relative = next(iter(reference_result["input_hashes"]))
    raw = ROOT / raw_relative
    if _sha(raw) != reference_result["input_hashes"][raw_relative]:
        raise RuntimeError("action04 raw hash changed")
    output.mkdir(parents=True)
    replay = output / "REPLAY"
    audit_path = output / "CHILD_AUDIT.json"
    time_path = output / "CHILD_TIME.txt"
    stdout_path = output / "CHILD_STDOUT.txt"
    stderr_path = output / "CHILD_STDERR.txt"
    contract = {
        "status": "FROZEN_BEFORE_DECODE",
        "command": literal_command,
        "action": "04_shoulder_left",
        "scope": "GROUP_REFERENCE_EPOCH_FIRST_5S_WITH_MEASURED_LINK_OVERHANG",
        "expected": {"groups": EXPECTED_GROUPS, "sweeps": EXPECTED_SWEEPS,
                     "valid_links": EXPECTED_LINKS},
        "observation_only": True,
        "raw_path": raw_relative,
        "raw_sha256": _sha(raw),
        "u5b": u5b_binding,
        "u6_fixture": u6_binding,
        "cross_covariance_policy": "UNAVAILABLE_NOT_PROPAGATED_UNLESS_EXPLICIT_FULL_AUGMENTED_OWNER",
        "limits": {"wall_s": 300, "rss_kib": RSS_CAP_KIB,
                   "evidence_bytes": EVIDENCE_CAP_BYTES},
        "calibrated_R": False, "scientific_pass": False,
        "production_ready": False, "no_retry": True,
    }
    _write_json(output / "CONTRACT.json", contract)
    child_command = [
        sys.executable, str(Path(__file__).resolve()), "--child",
        "--replay-output", str(replay), "--audit-output", str(audit_path),
    ]
    env = dict(os.environ)
    env.update({"OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1",
                "MKL_NUM_THREADS": "1", "NUMEXPR_NUM_THREADS": "1",
                "PYTHONPATH": "src:tools:."})
    with stdout_path.open("w") as stdout, stderr_path.open("w") as stderr:
        completed = subprocess.run(
            ["/usr/bin/time", "-v", "-o", str(time_path), *child_command],
            cwd=ROOT, env=env, stdout=stdout, stderr=stderr,
            timeout=300, check=False,
        )
    external_rss = _external_rss(time_path)
    audit = json.loads(audit_path.read_text()) if audit_path.is_file() else {}
    replay_result = json.loads((replay / "RESULT.json").read_text())
    reference_rows = U5B / "SWEEPS.jsonl"
    replay_rows = replay / "SWEEPS.jsonl"
    rows_byte_identical = replay_rows.read_bytes() == reference_rows.read_bytes()
    coverage = audit.get("coverage", {})
    covariance_keys = (
        "sensor_r_finite_symmetric_psd", "total_prior_r_finite_symmetric_psd",
        "robust_effective_r_finite_symmetric_psd", "prior_s_finite_symmetric_psd",
        "effective_s_finite_symmetric_psd", "zero_valid_link_deletion",
    )
    rejections = int(sum(
        count for reason, count in replay_result["update_reasons"].items()
        if reason != "ACCEPTED"))
    wall_s = time.perf_counter() - started
    current_memory = _proc_memory()
    output_bytes = sum(path.stat().st_size for path in output.rglob("*") if path.is_file())
    passed = bool(
        completed.returncode == 0 and audit.get("returncode") == 0
        and rows_byte_identical
        and replay_result["uwb_groups"] == EXPECTED_GROUPS
        and replay_result["node_sweeps"] == EXPECTED_SWEEPS
        and replay_result["structurally_valid_links"] == EXPECTED_LINKS
        and coverage.get("sweeps") == EXPECTED_SWEEPS
        and coverage.get("links") == EXPECTED_LINKS
        and all(coverage.get(key) == EXPECTED_SWEEPS for key in covariance_keys)
        and coverage.get("cross_covariance_unavailable") == EXPECTED_SWEEPS
        and coverage.get("cross_covariance_supplied") == 0
        and replay_result["range_deletions"] == 0
        and external_rss < RSS_CAP_KIB
        and audit.get("rusage_self_maxrss_kib", math.inf) < RSS_CAP_KIB
        and current_memory.get("VmHWM_kib", math.inf) < RSS_CAP_KIB
        and wall_s <= 300 and output_bytes < EVIDENCE_CAP_BYTES
    )
    result = {
        "schema": "biospur.c2.offline_uncertainty.u6.action04_first5s.v1",
        "status": "U6_ACTION04_FIRST5S_COVERAGE_COMPLETE" if passed else "BLOCKED_U6_ACTION04_COVERAGE",
        "execution_class": "OFFLINE_OBSERVATION_ONLY",
        "calibrated_R": False, "scientific_pass": False,
        "production_ready": False, "accuracy_claim": False,
        "HXX_opened": False, "raw_opened": True,
        "action": "04_shoulder_left",
        "metric_scope": "GROUP_REFERENCE_EPOCH_FIRST_5S_WITH_MEASURED_LINK_OVERHANG",
        "u5b": u5b_binding, "u6_fixture": u6_binding,
        "raw_path": raw_relative, "raw_sha256": _sha(raw),
        "groups": replay_result["uwb_groups"],
        "sweeps": replay_result["node_sweeps"],
        "structurally_valid_links": replay_result["structurally_valid_links"],
        "range_deletions": replay_result["range_deletions"],
        "decision_rejections": rejections,
        "decision_reasons": replay_result["update_reasons"],
        "u5b_rows_byte_identical": rows_byte_identical,
        "u5b_reference_rows_sha256": _sha(reference_rows),
        "replay_rows_sha256": _sha(replay_rows),
        "coverage": coverage,
        "maximum_absolute_recomputation_errors": audit.get(
            "maximum_absolute_recomputation_errors", {}),
        "cross_covariance_unavailable": coverage.get("cross_covariance_unavailable"),
        "cross_covariance_supplied": coverage.get("cross_covariance_supplied"),
        "observation_only_no_estimator_feedback": True,
        "child_internal_maximum_rss_kib": audit.get("rusage_self_maxrss_kib"),
        "child_external_maximum_rss_kib": external_rss,
        "parent_memory": current_memory,
        "wall_s": wall_s, "output_bytes_before_summary": output_bytes,
        "no_retry": True,
    }
    _write_json(output / "RESULT.json", result)
    (output / "COMMAND.txt").write_text(literal_command.rstrip() + "\n")
    (output / "REPORT.md").write_text(
        "# U6 action04 first-five-second uncertainty coverage\n\n"
        f"Status: `{result['status']}`. The observation-only adapter covered "
        f"{result['sweeps']}/{EXPECTED_SWEEPS} sweeps and "
        f"{result['structurally_valid_links']} structurally valid links. U5B rows "
        f"were byte-identical: {rows_byte_identical}. Decision rejections: {rejections}. "
        f"Root--bias cross covariance was unavailable for "
        f"{result['cross_covariance_unavailable']} sweeps and supplied for "
        f"{result['cross_covariance_supplied']}.\n\n"
        "This is uncalibrated offline coverage only. It does not fit thresholds, delete "
        "ranges, affect updates, promote U2/production, or support accuracy/scientific claims.\n"
    )
    digest = _seal(output)
    final_bytes = sum(path.stat().st_size for path in output.rglob("*") if path.is_file())
    print(json.dumps({"status": result["status"], "seal_sha256": digest,
                      "wall_s": wall_s, "external_rss_kib": external_rss,
                      "bytes": final_bytes}, sort_keys=True))
    return 0 if passed else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--child", action="store_true")
    parser.add_argument("--replay-output", type=Path)
    parser.add_argument("--audit-output", type=Path)
    args = parser.parse_args()
    if args.child:
        if args.output is not None or args.replay_output is None or args.audit_output is None:
            raise SystemExit("child arguments invalid")
        return _child(args.replay_output, args.audit_output)
    if args.output is None or args.replay_output is not None or args.audit_output is not None:
        raise SystemExit("parent arguments invalid")
    literal = (
        "timeout --signal=TERM --kill-after=5s 300s env PYTHONPATH=src:tools:. "
        ".venv-v0/bin/python tools/run_c2_offline_uncertainty_u6_action04.py "
        f"--output {args.output}"
    )
    return _parent(args.output.resolve(), literal)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BaseException:
        traceback.print_exc()
        raise
