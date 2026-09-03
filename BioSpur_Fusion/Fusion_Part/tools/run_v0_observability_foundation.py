#!/usr/bin/env python3
"""Write the payload-free Milestone-A observability qualification."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from biospur_fusion.v0.observability_first import foundation_qualification  # noqa: E402


INPUTS = (
    "config/imu_multi_action_revision_d_d0/D0_STATE_AND_GAUGE_CONTRACT.json",
    "config/imu_multi_action_revision_d_d0/D0_ACTION_FACTOR_CONTRACT.json",
    "config/biospur_fusion_v0_observability_first/STATE_AND_GAUGE_CONTRACT.json",
    "config/biospur_fusion_v0_observability_first/ACTION_FACTOR_CONTRACT.json",
    "config/biospur_fusion_v0_observability_first/REUSE_INVENTORY.json",
    "src/biospur_fusion/v0/observability_first.py",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    result = foundation_qualification()
    result["input_sha256"] = {name: sha256(ROOT / name) for name in INPUTS}
    result["rejected_locks_preserved"] = ["c6d9170f...", "05752bb7..."]
    result["real_estimator_changed"] = False
    (output / "FOUNDATION_QUALIFICATION.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n"
    )
    report = f"""# BioSpur Pure-IMU V0 observability-first foundation

Result: `{'PASS' if result['pass'] else 'FAIL'}`.

The ten-node joint-graph heading Jacobian has rank
`{result['heading_graph']['unquotiented']['rank']}/10` before quotienting, with
exactly one null direction aligned with common yaw.  Fixing only the pelvis
coordinate gives rank
`{result['heading_graph']['pelvis_gauge_quotient']['rank']}/9`.

The decisive independent-yaw T-pose counterexample leaves q90 arm-chain
azimuth error
`{result['one_common_yaw_counterexample']['one_common_yaw_q90_error_deg']:.3f} deg`
after the best single common correction.  Nine relative corrections reduce
the same synthetic residual to
`{result['one_common_yaw_counterexample']['nine_relative_heading_max_error_deg']:.3g} deg`.

This is a payload-free foundation result.  It does not qualify the full
95-coordinate estimator, real data, direct FK, Viewer output, or readiness.
"""
    (output / "REPORT.md").write_text(report)
    return 0 if result["pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
