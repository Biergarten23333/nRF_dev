"""Execute the preregistered V0/V1/V2 official OpenSense 00/02 study."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Mapping

import numpy as np

from biospur_fusion.c2_3a_kinematics import load_frozen_c2_3a

from .adapter import sha256_file
from .causal_adapter import (
    BINDING_SHA256,
    INPUT_SHA256_BY_CAPTURE,
    ZERO_MODEL_SHA256,
    configure_deterministic_binding_model,
    run_official_basis_variant,
)
from .causal_render import render_causal_comparison, write_render_manifest
from .validate import validate_orientation_replay


PRIOR_SHA256SUMS_SHA256 = (
    "f6070211c8dacfc6e37ead87a93dba56b6076805d13e84a3cc1d60962b271175"
)
EPISODES: Mapping[str, str] = {
    "00_initial_still": "00",
    "02_t_pose": "01",
}
VARIANTS = ("V0_ZERO", "V1_BASIS_ONLY", "V2_DETERMINISTIC_BINDING")


def _distribution(values: np.ndarray) -> dict[str, float]:
    values = np.asarray(values, dtype=float)
    result_rad = {
        "mean": float(np.mean(values)),
        "median": float(np.median(values)),
        "p95": float(np.quantile(values, 0.95, method="linear")),
        "max": float(np.max(values)),
    }
    return {
        **{f"{name}_rad": value for name, value in result_rad.items()},
        **{
            f"{name}_deg": float(np.degrees(value))
            for name, value in result_rad.items()
        },
    }


def _official_error_summary(error_path: Path) -> dict[str, object]:
    import opensim as osim

    table = osim.TimeSeriesTable(str(error_path.resolve()))
    labels = list(table.getColumnLabels())
    values = np.stack(
        [np.asarray(table.getDependentColumn(label).to_numpy()) for label in labels],
        axis=1,
    )
    if table.getNumRows() != 701 or not np.all(np.isfinite(values)):
        raise ValueError(f"incomplete/nonfinite official errors: {error_path}")
    return {
        "owner": "native OpenSim orientation-error STO; descriptive aggregation only",
        "rows": int(table.getNumRows()),
        "sensors": labels,
        "all_finite": True,
        "quantile_method": "numpy_linear",
        "overall": _distribution(values.reshape(-1)),
        "per_sensor": {
            label: _distribution(values[:, index])
            for index, label in enumerate(labels)
        },
        "source": str(error_path.resolve()),
        "source_sha256": sha256_file(error_path),
    }


def _differences(candidate, reference):
    statistics = (
        "mean_rad",
        "median_rad",
        "p95_rad",
        "max_rad",
        "mean_deg",
        "median_deg",
        "p95_deg",
        "max_deg",
    )
    return {name: float(candidate[name] - reference[name]) for name in statistics}


def _variant_error_path(prior_root: Path, output_root: Path, variant: str, capture: str):
    if variant == "V0_ZERO":
        return (
            prior_root
            / "real_pilot_00_02"
            / capture
            / "official_output/official_ik.sto_orientationErrors.sto"
        )
    return output_root / variant / capture / "official_output/official_ik.sto_orientationErrors.sto"


def _variant_motion_model(prior_root: Path, output_root: Path, v2_model: Path, capture: str):
    zero_model = prior_root / "real_pilot_00_02/model/c2_official_configured.osim"
    return {
        "V0_ZERO": (
            zero_model,
            prior_root
            / "real_pilot_00_02"
            / capture
            / "official_output/official_ik.sto",
        ),
        "V1_BASIS_ONLY": (
            zero_model,
            output_root
            / "V1_BASIS_ONLY"
            / capture
            / "official_output/official_ik.sto",
        ),
        "V2_DETERMINISTIC_BINDING": (
            v2_model,
            output_root
            / "V2_DETERMINISTIC_BINDING"
            / capture
            / "official_output/official_ik.sto",
        ),
    }


def run_causal_study(workspace: Path, evidence_root: Path, prior_root: Path) -> dict:
    workspace = workspace.resolve()
    evidence_root = evidence_root.resolve()
    prior_root = prior_root.resolve()
    contract_path = evidence_root / "VARIANT_CONTRACT.json"
    binding_path = evidence_root / "precode_derivation/DETERMINISTIC_FRAME_BINDING.json"
    if sha256_file(prior_root / "SHA256SUMS") != PRIOR_SHA256SUMS_SHA256:
        raise ValueError("prior V0 evidence seal changed")
    if sha256_file(binding_path) != BINDING_SHA256:
        raise ValueError("pre-code V2 frame binding changed")
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    binding = json.loads(binding_path.read_text(encoding="utf-8"))

    output_root = evidence_root / "real_00_02_v1_v2"
    output_root.mkdir(parents=True, exist_ok=True)
    zero_model = prior_root / "real_pilot_00_02/model/c2_official_configured.osim"
    if sha256_file(zero_model) != ZERO_MODEL_SHA256:
        raise ValueError("V0 zero-offset configured model changed")
    v2_model = output_root / "model/v2_deterministic_binding.osim"
    model_manifest = configure_deterministic_binding_model(
        zero_model,
        binding_path,
        v2_model,
        output_root / "model/model_configuration_opensim.log",
    )

    frozen = load_frozen_c2_3a(workspace=workspace)
    input_replay = {}
    runs: dict[str, object] = {variant: {} for variant in VARIANTS}
    for capture, frozen_key in EPISODES.items():
        orientations = (
            prior_root
            / "real_pilot_00_02"
            / capture
            / "input/frozen_orientations.sto"
        )
        if sha256_file(orientations) != INPUT_SHA256_BY_CAPTURE[capture]:
            raise ValueError(f"immutable input changed for {capture}")
        replay = validate_orientation_replay(frozen.episodes[frozen_key], orientations)
        if not replay["passed"] or replay["row_count"] != 701:
            raise ValueError(f"OpenSim parser replay failed for {capture}")
        input_replay[capture] = {
            **replay,
            "source": str(orientations.resolve()),
            "source_sha256": sha256_file(orientations),
        }
        prior_manifest = (
            prior_root
            / "real_pilot_00_02"
            / capture
            / "official_output/official_ik_manifest.json"
        )
        runs["V0_ZERO"][capture] = {
            "execution": "READ_ONLY_PRIOR_HASH_BOUND_NO_RERUN",
            "manifest": str(prior_manifest.resolve()),
            "manifest_sha256": sha256_file(prior_manifest),
        }
        for variant, model_path in (
            ("V1_BASIS_ONLY", zero_model),
            ("V2_DETERMINISTIC_BINDING", v2_model),
        ):
            runs[variant][capture] = run_official_basis_variant(
                variant=variant,
                capture_label=capture,
                configured_model=model_path,
                orientations_file=orientations,
                output_dir=output_root / variant / capture / "official_output",
                first_time_s=0.0,
                last_time_s=35.0,
            )

    summaries = {
        variant: {
            capture: _official_error_summary(
                _variant_error_path(prior_root, output_root, variant, capture)
            )
            for capture in EPISODES
        }
        for variant in VARIANTS
    }
    deltas = {}
    for variant in ("V1_BASIS_ONLY", "V2_DETERMINISTIC_BINDING"):
        deltas[variant] = {}
        for capture in EPISODES:
            candidate = summaries[variant][capture]
            reference = summaries["V0_ZERO"][capture]
            deltas[variant][capture] = {
                "overall": _differences(candidate["overall"], reference["overall"]),
                "per_sensor": {
                    sensor: _differences(
                        candidate["per_sensor"][sensor],
                        reference["per_sensor"][sensor],
                    )
                    for sensor in reference["sensors"]
                },
            }
    metrics = {
        "schema": "biospur-c2-official-opensense-causal-errors-v1",
        "all_rows_no_exclusions": True,
        "summaries": summaries,
        "candidate_minus_v0": deltas,
    }
    metrics_path = output_root / "OFFICIAL_ORIENTATION_ERROR_COMPARISON.json"
    metrics_path.write_text(
        json.dumps(metrics, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    render_result = {
        "schema": "biospur-c2-official-opensense-causal-render-v1",
        "episodes": {},
    }
    for capture, frozen_key in EPISODES.items():
        render_result["episodes"][capture] = render_causal_comparison(
            frozen=frozen,
            capture_label=capture,
            frozen_episode=frozen_key,
            variant_motion_model=_variant_motion_model(
                prior_root, output_root, v2_model, capture
            ),
            binding=binding,
            output_dir=output_root / "rendering",
        )
    render_manifest_path = output_root / "rendering/RENDER_MANIFEST.json"
    write_render_manifest(render_result, render_manifest_path)

    result = {
        "schema": "biospur-c2-official-opensense-causal-study-v1",
        "scientific_pass": False,
        "contract": str(contract_path),
        "contract_sha256": sha256_file(contract_path),
        "prior_evidence": str(prior_root),
        "prior_sha256s_sha256": sha256_file(prior_root / "SHA256SUMS"),
        "model": model_manifest,
        "input_replay": input_replay,
        "runs": runs,
        "metrics": str(metrics_path.resolve()),
        "metrics_sha256": sha256_file(metrics_path),
        "render_manifest": str(render_manifest_path.resolve()),
        "render_manifest_sha256": sha256_file(render_manifest_path),
        "variants": list(VARIANTS),
        "episodes": dict(EPISODES),
        "extend_19_plus_2": False,
        "uwb_consumed": False,
        "custom_solver_or_residual": False,
        "imu_placer_used": False,
        "action_or_pixel_fit": False,
    }
    manifest_path = output_root / "CAUSAL_STUDY_MANIFEST.json"
    manifest_path.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--prior-root", type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            run_causal_study(args.workspace, args.evidence_root, args.prior_root),
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
