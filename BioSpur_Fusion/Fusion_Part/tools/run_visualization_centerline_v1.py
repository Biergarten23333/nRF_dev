#!/usr/bin/env python3
"""Compile VISUALIZATION_CENTERLINE_V1 inputs; never open capture payloads."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "Fusion_Part/src"))

from biospur_fusion.visualization.centerline_v1 import (  # noqa: E402
    FORBIDDEN_PATH_PARTS,
    VisualizationInputError,
    compile_visualization_inputs,
    read_csv,
    sha256,
)


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _assert_safe_output(path: Path) -> None:
    resolved = path.resolve()
    collision = sorted({part.lower() for part in resolved.parts} & FORBIDDEN_PATH_PARTS)
    if collision or any(part.lower().startswith("analysis_body_fusion_v") for part in resolved.parts):
        raise VisualizationInputError(f"capture/historical output path is forbidden: {collision or resolved}")
    if resolved.exists() and (not resolved.is_dir() or any(resolved.iterdir())):
        raise VisualizationInputError(f"output directory must be new or empty: {resolved}")


def _verify_immutable_binding(binding: dict) -> dict[str, bool]:
    checks: dict[str, bool] = {}
    for name, value in binding["immutable_sources"].items():
        path = ROOT / value["path"]
        checks[name] = path.is_file() and sha256(path) == value["sha256"]
    if not all(checks.values()):
        raise VisualizationInputError(f"immutable V4.1 input-preparation binding changed: {checks}")
    return checks


def main() -> int:
    config = ROOT / "Fusion_Part/config"
    v1 = config / "visualization_centerline_v1"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--subject-measurements",
        type=Path,
        default=v1 / "v47_visualization_subject_measurements.csv",
        help="filled copy of the visualization-only 12-row subject template",
    )
    parser.add_argument(
        "--hardware-measurements",
        type=Path,
        default=v1 / "v47_visualization_hardware_measurements.csv",
        help="filled copy of the direct observable hardware template",
    )
    parser.add_argument(
        "--shoe-measurements",
        type=Path,
        help="optional filled copy; missing feet do not block torso/limb centerline",
    )
    parser.add_argument(
        "--wearing-convention",
        type=Path,
        default=v1 / "v47_shared_wearing_convention.csv",
        help="filled copy of the shared-wearing template",
    )
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        _assert_safe_output(args.out)
        binding = _json(v1 / "v47_input_binding.json")
        immutable_checks = _verify_immutable_binding(binding)
        gates = _json(v1 / "visualization_gates_v1.json")
        hardware = _json(config / "body_calibration_v4_1/input_preparation/HARDWARE_PROVENANCE.json")
        subject = read_csv(args.subject_measurements)
        hardware_measurements = read_csv(args.hardware_measurements)
        wearing = read_csv(args.wearing_convention)
        shoe = read_csv(args.shoe_measurements) if args.shoe_measurements else None
        result = compile_visualization_inputs(
            subject,
            hardware_measurements,
            wearing,
            hardware,
            gates,
            shoe_rows=shoe,
        )
        result["immutable_binding_checks"] = immutable_checks
        result["source_files"] = {
            "subject_measurements": {"path": str(args.subject_measurements.resolve()), "sha256": sha256(args.subject_measurements)},
            "hardware_measurements": {"path": str(args.hardware_measurements.resolve()), "sha256": sha256(args.hardware_measurements)},
            "wearing_convention": {"path": str(args.wearing_convention.resolve()), "sha256": sha256(args.wearing_convention)},
            "shoe_measurements": (
                {"path": str(args.shoe_measurements.resolve()), "sha256": sha256(args.shoe_measurements)}
                if args.shoe_measurements
                else None
            ),
        }
        args.out.mkdir(parents=True, exist_ok=True)
        output = args.out / "VISUALIZATION_INPUT_READINESS.json"
        output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (args.out / "SHA256SUMS").write_text(
            f"{sha256(output)}  {output.name}\n", encoding="utf-8"
        )
    except (VisualizationInputError, OSError, ValueError, KeyError) as exc:
        print(json.dumps({"verdict": "VISUALIZATION_INPUT_INVALID", "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps({
        "SCIENTIFIC_CENTERLINE": result["product_separation"]["SCIENTIFIC_CENTERLINE"],
        "VISUALIZATION_CENTERLINE": result["product_separation"]["VISUALIZATION_CENTERLINE"],
        "output": str(args.out.resolve()),
        "calibration_ledger_opened": False,
        "walk_opened": False,
        "final_still_opened": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
