#!/usr/bin/env python3
"""Run the independent physical-graph synthetic qualification gate."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from biospur_fusion.v0.contracts import dump_json
from biospur_fusion.v0.physical_graph_synthetic import qualify_synthetic_physical_graph


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = qualify_synthetic_physical_graph()
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError(output)
    dump_json(output, result)
    output.chmod(0o444)
    print(json.dumps({
        "output": str(output),
        "pass": result["pass"],
        "gates": result["gates"],
        "recovery_summary": {
            "median_heading_error_deg": result["recovery"]["median_heading_error_deg"],
            "maximum_heading_error_deg": result["recovery"]["maximum_heading_error_deg"],
            "measured_segment_length_error_m": result["recovery"][
                "measured_segment_length_error_m"
            ],
        },
    }, indent=2, sort_keys=True))
    if not result["pass"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
