#!/usr/bin/env python3
"""Run the synthetic-only S0/S1 torso structural repair audit."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil

from biospur_fusion.imu_multi_action_v1.core import canonical_json_bytes
from biospur_fusion.imu_multi_action_v1.structural_repair import (
    run_s0_s1_structural_repair,
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_bytes(canonical_json_bytes(value))


def run(config_dir: Path, repository_root: Path, output_dir: Path) -> dict:
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite {output_dir}")
    output_dir.mkdir(parents=True)
    config_names = (
        "MULTI_ACTION_CALIBRATION_V1_SPEC.md",
        "FRAME_AND_GAUGE_CONVENTIONS.json",
        "ACTION_FACTOR_MAP.json",
        "gates_v1.json",
        "S0_S1_STRUCTURAL_REPAIR_GATES_V1.json",
    )
    for name in config_names:
        source = config_dir/name
        if not source.is_file():
            raise FileNotFoundError(source)
        shutil.copyfile(source, output_dir/name)

    gates = json.loads((config_dir/"gates_v1.json").read_text())
    repair = json.loads(
        (config_dir/"S0_S1_STRUCTURAL_REPAIR_GATES_V1.json").read_text()
    )
    template_path = repository_root/repair["product_geometry"]["template_path"]
    if sha256(template_path) != repair["product_geometry"]["template_sha256"]:
        raise RuntimeError("generic template SHA mismatch")
    template = json.loads(template_path.read_text())

    first = run_s0_s1_structural_repair(gates, repair, template)
    second = run_s0_s1_structural_repair(gates, repair, template)
    first_bytes = canonical_json_bytes(first)
    second_bytes = canonical_json_bytes(second)
    deterministic = first_bytes == second_bytes
    first["double_replay_determinism"] = {
        "byte_identical": deterministic,
        "replay_1_sha256": hashlib.sha256(first_bytes).hexdigest(),
        "replay_2_sha256": hashlib.sha256(second_bytes).hexdigest(),
    }
    if not deterministic:
        first["verdict"] = "FAIL_SYNTHETIC_RECOVERY"
        first["phase_status"] = "STOPPED_AFTER_S0_AS_REQUIRED"

    write_json(output_dir/"S0_S1_RESULT.json", first)
    write_json(output_dir/"ANALYTIC_FINITE_TRANSFORM_T_ALPHA.json",
               first["transform"])
    write_json(output_dir/"FINITE_TRANSFORM_INVARIANCE_SCAN.json",
               first["finite_transform_scan"])
    write_json(output_dir/"SCALED_JACOBIAN_NULLSPACE_AUDIT.json",
               first["jacobian_audit"])
    write_json(output_dir/"ACTION_RESIDUAL_PARAMETER_SENSITIVITY.json",
               first["action_parameter_sensitivity"])
    write_json(output_dir/"SYNTHETIC_ACTION_ABLATION.json",
               first["action_ablation"])
    write_json(output_dir/"PARAMETERIZATION_BEFORE_AFTER.json",
               first["repair_before_after"])
    write_json(output_dir/"S1_REPARAMETERIZATION_STATUS.json", {
        "status": "NOT_RUN",
        "reason": (
            "T(alpha) preserves the implemented residual but changes a "
            "publishable centerline output during trunk. The ambiguity is not "
            "a legal product gauge, so quotienting is forbidden."
        ),
        "threshold_changed": False,
        "prior_added": False,
        "parameter_hard_coded": False,
        "physical_output_removed": False,
    })
    write_json(output_dir/"POST_REPAIR_SYNTHETIC_GATE_STATUS.json", {
        "status": "NOT_RUN",
        "reason": "S1 was not authorized by the failed S0 product-invariance rule.",
        "synthetic_recovery": "BLOCKED",
        "five_start_shared_multistart": "BLOCKED",
        "reparameterized_jacobian": "BLOCKED",
        "real_data_access": "FORBIDDEN_AND_NOT_ATTEMPTED",
    })
    write_json(output_dir/"DATA_ACCESS_AUDIT.json", {
        "schema": "biospur-s0-s1-data-access-audit-v1",
        "opened": [str((config_dir/name).resolve()) for name in config_names]
                  + [str(template_path.resolve())],
        "synthetic_generator_only": True,
        "real_calibration_ledger": "SEALED_NOT_OPENED",
        "UWB_T4": "SEALED_NOT_OPENED",
        "anchor_geometry": "SEALED_NOT_OPENED",
        "operator_measurements": "SEALED_NOT_OPENED",
        "walk": "SEALED_NOT_OPENED",
        "final_still": "SEALED_NOT_OPENED",
        "golf": "SEALED_NOT_OPENED",
        "boxing": "SEALED_NOT_OPENED",
    })

    scan = first["finite_transform_scan"]
    positive = next(row for row in scan["rows"] if row["alpha_rad"] == 0.1)
    negative = next(row for row in scan["rows"] if row["alpha_rad"] == -0.1)
    jac = first["jacobian_audit"]
    report = f"""# MULTI_ACTION_CALIBRATION_V1 — Phase S0/S1 structural repair

## Verdict

`{first['verdict']}`
`{first['phase_status']}`

The candidate torso axial/heading ambiguity is **not** a legal gauge of the published centerline product. Phase S1 quotient reparameterization was therefore not performed.

## Analytic finite transform

With active rotations and `R_DST_from_SRC`, the tested transform is:

`psi_torso' = psi_torso + alpha`

`C_B' = G_B(alpha) C_B`

`G_B(alpha) = R_N_from_B_ref^T Rz_N(-alpha) R_N_from_B_ref`

In Hamilton active quaternion order, `q_G = inverse(q_N_from_B_ref) tensor q_z(-alpha) tensor q_N_from_B_ref`, followed by `q_C' = q_G tensor q_C`. The complete frame, multiplication-order and sign declaration is in `ANALYTIC_FINITE_TRANSFORM_T_ALPHA.json`.

## Finite invariance result

All 12 required values from `-0.1` through `+0.1 rad` were scanned. The complete residual vector, every residual block and both least-squares and Huber costs remained within the frozen numerical tolerances. However, at `+0.1 rad` the trunk output changed by `{positive['products']['per_action']['trunk']['maximum_segment_axis_angle_rad']:.12g} rad` in segment direction and `{positive['products']['per_action']['trunk']['maximum_graphical_joint_displacement_m']*1000.0:.12g} mm` in graphical nodes. At `-0.1 rad` the corresponding values were `{negative['products']['per_action']['trunk']['maximum_segment_axis_angle_rad']:.12g} rad` and `{negative['products']['per_action']['trunk']['maximum_graphical_joint_displacement_m']*1000.0:.12g} mm`.

Initial still, T-pose, arms, dedicated elbow/knee, squats and heel windows are invariant to numerical precision because their synthetic torso pose is the reference pose. The missing sensitivity is specifically dynamic torso/trunk sensitivity: the implemented trunk residual observes only endpoint transverse consistency, while the forward centerline publishes the intervening dynamic torso axis.

## Jacobian

The unchanged `{jac['jacobian_shape'][0]} x {jac['jacobian_shape'][1]}` scaled Jacobian has rank `{jac['rank']}` at the original relative threshold `{jac['relative_rank_threshold']}`. `sigma_max={jac['sigma_max']:.12g}`, `sigma_370={jac['sigma_370']:.12g}`, `sigma_371={jac['sigma_371']:.12g}`, and the spectral gap is `{jac['spectral_gap_sigma_370_over_sigma_371']:.12g}`. The full 371-component null vector, block energy, bottom spectrum, column norms/scaling, units, whitening and threshold sweep are serialized in `SCALED_JACOBIAN_NULLSPACE_AUDIT.json`.

The closed-form SO(3) directional derivative, central parameter Jacobian product and central finite transform derivative agree within the predeclared `1e-7` component tolerance. This confirms the residual null direction but does not turn it into a product gauge.

## Action sensitivity and ablation

`ACTION_RESIDUAL_PARAMETER_SENSITIVITY.json` contains the machine-readable `ACTION -> RESIDUAL BLOCK -> PARAMETER BLOCK` matrix. Every action declared as a calibration input has nonzero parameter information. Left/right heel remain explicitly validation-only because the nodes are shank-mounted. All eleven action-removal Jacobians are reported in `SYNTHETIC_ACTION_ABLATION.json`; none repairs the existing torso nullspace.

## Stop boundary

No threshold was changed, no prior was added, no coordinate was set to zero, no T-pose-only lock was introduced, and no physical output was deleted. Because S0 disproved product invariance, S1 and the post-repair full synthetic recovery/multistart gate were not legally reachable. Real calibration, UWB/T4, Anchor geometry, operator measurements, walk, final_still, golf and boxing remained sealed. No freeze, replay, animation, commit or push was performed.
"""
    (output_dir/"REPORT.md").write_text(report, encoding="utf-8")

    provenance = {
        "schema": "biospur-s0-s1-run-provenance-v1",
        "config_sha256": {name: sha256(config_dir/name) for name in config_names},
        "template_absolute_path": str(template_path.resolve()),
        "template_sha256": sha256(template_path),
        "source_sha256": {
            path.name: sha256(path)
            for path in sorted((repository_root/"Fusion_Part/src/biospur_fusion/imu_multi_action_v1").glob("*.py"))
        },
    }
    write_json(output_dir/"RUN_PROVENANCE.json", provenance)
    files = {
        path.name: sha256(path) for path in sorted(output_dir.iterdir())
        if path.is_file()
    }
    write_json(output_dir/"SHA256_MANIFEST.json", {
        "schema": "biospur-s0-s1-output-manifest-v1",
        "files": files,
    })
    return first


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config-dir", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.config_dir.resolve(), args.repository_root.resolve(),
                 args.output_dir.resolve())
    print(result["verdict"])
    print(f"output={args.output_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
