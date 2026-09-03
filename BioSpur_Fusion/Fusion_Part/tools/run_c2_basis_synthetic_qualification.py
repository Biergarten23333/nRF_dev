#!/usr/bin/env python3
"""Run and preserve the C2 basis independent synthetic qualification."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil

import numpy as np

from biospur_fusion.v0.c2_basis.contracts import load_config
from biospur_fusion.v0.c2_basis.geometry import load_body_geometry
from biospur_fusion.v0.c2_basis.qualification import qualify_case, run_synthetic_qualification
from biospur_fusion.v0.c2_basis.synthetic_truth import generate_synthetic_truth


ROOT = Path(__file__).resolve().parents[1]


def _json_default(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(type(value).__name__)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--single", action="store_true")
    args = parser.parse_args()
    nrf_free = shutil.disk_usage("/mnt/nrf_ssd").free
    root_free = shutil.disk_usage("/").free
    projected = 100 * 1024 * 1024
    if nrf_free < 100 * 1024**3 or root_free < 40 * 1024**3 or projected > 5 * 1024**3:
        raise SystemExit("disk gate failed before synthetic qualification")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output = args.output or ROOT / "logs" / f"c2_basis_synthetic_qualification_{stamp}" / "qualification.json"
    output.parent.mkdir(parents=True, exist_ok=False)
    geometry = load_body_geometry(ROOT)
    config = load_config(ROOT)
    try:
        if args.single:
            case, _, _ = qualify_case(
                "randomized_mounts_seed_20260829",
                generate_synthetic_truth(seed=20260829), geometry, config, seed=9200,
            )
            report = {
                "schema": "biospur-c2-independent-synthetic-smoke-v1",
                "cases": [case],
                "mutation_controls": [],
                "gates": {"single_positive_case_pass": case["pass"]},
                "pass": case["pass"],
            }
        else:
            report = run_synthetic_qualification(geometry, config)
    except Exception as exc:
        report = {
            "schema": "biospur-c2-independent-synthetic-exception-v1",
            "cases": [],
            "mutation_controls": [],
            "gates": {"unhandled_exception": False},
            "pass": False,
            "exception_type": type(exc).__name__,
            "exception": str(exc),
        }
    report["disk_gate"] = {
        "nrf_ssd_free_bytes_before": nrf_free,
        "root_free_bytes_before": root_free,
        "projected_growth_bytes": projected,
        "pass": True,
    }
    output.write_text(
        json.dumps(report, indent=2, sort_keys=True, default=_json_default) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "output": str(output),
        "pass": report["pass"],
        "gates": report["gates"],
        "case_summary": [{
            "name": case["name"],
            "pass": case["pass"],
            "maximum_heading_truth_error_deg": case["maximum_heading_truth_error_deg"],
            "maximum_mount_truth_error_deg": case["maximum_mount_truth_error_deg"],
            "maximum_axial_offset_truth_error_m": case["maximum_axial_offset_truth_error_m"],
            "failed_gates": [name for name, passed in case["gates"].items() if not passed],
        } for case in report["cases"]],
        "failed_mutations": [
            row["name"] for row in report["mutation_controls"] if not row["rejected"]
        ],
    }, indent=2))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
