#!/usr/bin/env python3
"""Synthetic-only U5A qualification for the C2 tight raw-range owner."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import resource
import subprocess
import sys
import time

import numpy as np

from biospur_fusion.c2_uwb_root_world.tight_range import linearize_raw_range_factors
from biospur_fusion.c2_uwb_root_world.u0 import ClockModel, UwbRow
from biospur_fusion.root_r3.models import RootState


ROOT = Path(__file__).resolve().parents[1]
OWNED = (
    ROOT / "src/biospur_fusion/c2_uwb_root_world/tight_range.py",
    ROOT / "tests/test_c2_tight_raw_range.py",
    ROOT / "tests/test_c2_tight_range_linearization.py",
    Path(__file__).resolve(),
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _benchmark() -> dict[str, object]:
    anchors = np.array([
        [0, 0, 0], [4, 0, 0], [4, 3, 0], [0, 3, 0],
        [0, 0, 2], [4, 0, 2], [4, 3, 2], [0, 3, 2],
    ], dtype=float)
    truth = np.array([2.0, 1.2, 0.9])
    ranges = np.linalg.norm(anchors - truth, axis=1)
    row = UwbRow(
        "BSFC2CC", 0, 1, 1, 1_000_000, 1_010_000, tuple(range(8)),
        tuple(int(round(x * 1000)) for x in ranges),
        tuple(1000 + 500 * i for i in range(8)), (100,) * 8, 0xFF)
    clock = ClockModel(0, 1000.0, 0.0, 0.0)
    epochs = [clock.seconds(row.strobe_us + 0.5 * x) for x in row.t_round_us]
    vector = np.zeros(9); vector[:3] = truth; vector[3:6] = [0.2, -0.1, 0.05]
    state = RootState(float(np.median(epochs)), vector, np.eye(9) * 0.04)
    samples = np.empty(20_000)
    for index in range(len(samples)):
        started = time.perf_counter_ns()
        out = linearize_raw_range_factors(
            state, row, anchors_m=anchors, clock=clock)
        samples[index] = (time.perf_counter_ns() - started) * 1e-6
    return {
        "calls": len(samples),
        "p50_ms": float(np.quantile(samples, 0.50)),
        "p99_ms": float(np.quantile(samples, 0.99)),
        "maximum_ms": float(np.max(samples)),
        "fixed_link_state_count": len(out.anchors),
        "raw_or_hxx_opened": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("output must not exist")
    args.output.mkdir(parents=True)
    started = time.perf_counter()
    before = {str(path.relative_to(ROOT)): _sha(path) for path in OWNED}
    command = [
        sys.executable, "-m", "pytest", "-q",
        "tests/test_c2_tight_raw_range.py",
        "tests/test_c2_tight_range_linearization.py",
    ]
    completed = subprocess.run(
        command, cwd=ROOT, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, timeout=180, check=False)
    (args.output / "TESTS.txt").write_text(
        "$ PYTHONPATH=src:tools:. " + " ".join(command) + "\n" + completed.stdout)
    after = {str(path.relative_to(ROOT)): _sha(path) for path in OWNED}
    benchmark = _benchmark() if completed.returncode == 0 else {}
    rss_kib = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    unchanged = before == after
    passed = bool(
        completed.returncode == 0 and unchanged and rss_kib < 300_000
        and benchmark.get("calls") == 20_000
    )
    result = {
        "schema": "biospur.c2.tight_range.u5a.synthetic.v2",
        "status": "READY_FOR_MONITOR_U5A_REVIEW" if passed else "BLOCKED_U5A_PREFLIGHT",
        "scientific_pass": False,
        "calibrated_R": False,
        "real_data_opened": False,
        "raw_or_hxx_opened": False,
        "action04_opened": False,
        "tests_returncode": completed.returncode,
        "source_hashes_unchanged": unchanged,
        "uncertainty_provenance": "PROVISIONAL_UNCALIBRATED_DIAGNOSTIC",
        "root_bias_cross_covariance_default": "UNAVAILABLE_NOT_PROPAGATED",
        "decision_total_sigma_owner": "sigma_m",
        "decision_sensor_only_sigma_owner": "sensor_sigma_m",
        "rejected_unseen_bias_state_allocated": False,
        "range_deletion": False,
        "benchmark": benchmark,
        "wall_s": time.perf_counter() - started,
        "maximum_rss_kib": rss_kib,
    }
    _write_json(args.output / "RESULT.json", result)
    _write_json(args.output / "HASHES.json", {"before": before, "after": after})
    (args.output / "COMMAND.txt").write_text(
        "PYTHONPATH=src:tools:. .venv-v0/bin/python tools/preflight_c2_tight_range_u5a.py "
        f"--output {args.output}\n")
    (args.output / "REPORT.md").write_text(
        "# U5A synthetic tight-range qualification\n\n"
        f"Status: `{result['status']}`. Focused tests returned {completed.returncode}; "
        f"20,000-call p99 was {benchmark.get('p99_ms')} ms and RSS was {rss_kib} KiB.\n\n"
        "This qualifies fixture mechanics only. Range R, the positive-tail scale, "
        "and root/bias cross-covariance remain uncalibrated; scientific and production "
        "claims are false. No raw, HXX, or action data were opened.\n")
    members = sorted(path for path in args.output.iterdir() if path.name != "SHA256SUMS")
    (args.output / "SHA256SUMS").write_text("".join(
        f"{_sha(path)}  {path.name}\n" for path in members))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
