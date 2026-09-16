#!/usr/bin/env python3
"""Freeze the Phase O1A implicit RF-field contract without opening sensor data."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import resource
import shutil
import sys
import time
from typing import Any

from biospur_fusion.c2_uwb_calibration.rf_shadow_field import (
    EMITTER_LOCAL_SEGMENTS,
    INTEGRATION_GAUSS_ORDER_PER_PARTITION,
    INTEGRATION_MAXIMUM_NODES,
    INTEGRATION_METHOD,
    INTEGRATION_MINIMUM_WIDTH_SUPPORT_SIGMA,
    INTEGRATION_REFERENCE_ABSOLUTE_TOLERANCE,
    INTEGRATION_REFERENCE_RELATIVE_TOLERANCE,
    LONGITUDINAL_SIGMOID_SHARPNESS,
    MORPHOLOGY_BOUNDS,
    MORPHOLOGY_PARAMETER_NAMES,
    NUISANCE_NAMES,
)


ROOT = Path(__file__).resolve().parents[1]
BASELINE_COMMIT = "59e0bf449c03ae041e9982f0dd5cd1f1c46f88c4"
SEED = 20260905
CANONICAL_ACTIONS = tuple(["00", *[f"{index:02d}" for index in range(2, 20)]])
UNAVAILABLE_ACTIONS = ("01",)
MIRROR_PAIRS = (("04", "05"), ("06", "07"), ("08", "09"),
                ("10", "11"), ("12", "13"), ("18", "19"))
CAPTURE_PHASE_UNITS = {
    "early": (("00",), ("02",), ("03",), ("04", "05")),
    "mid": (("06", "07"), ("08", "09"), ("10", "11"), ("12", "13")),
    "late": (("14",), ("15",), ("16",), ("17",), ("18", "19")),
}
EXCLUDED_PATHS = (".venv-v0", "PCB", "datasets", "reports", "tmp")
PREEXISTING_INTEGRITY_PATHS = (
    "tools/audit_c2_dynamic_other_body_occlusion_o0.py",
    "tests/test_c2_dynamic_other_body_occlusion_o0.py",
    "logs/c2_dynamic_other_body_occlusion_o0_20260905_173641/RESULT.json",
    "logs/c2_dynamic_other_body_occlusion_o0_20260905_173641/SHA256SUMS",
)
SOURCE_OWNER_PATHS = (
    "src/biospur_fusion/c2_uwb_calibration/antenna_los.py",
    "src/biospur_fusion/c2_uwb_calibration/frozen_body_proxy.py",
    "src/biospur_fusion/c2_uwb_calibration/shared_root.py",
    "src/biospur_fusion/c2_uwb_calibration/rf_shadow_field.py",
    "tools/preregister_c2_rf_shadow_field_o1a.py",
    "tests/test_c2_rf_shadow_field.py",
)
EVIDENCE_FILES = (
    "MODEL_CONTRACT.json", "MODEL_CONTRACT.md", "SPLIT.json",
    "ELIGIBILITY.json", "EVAL_CONTRACT.json", "FILE_ALLOWLIST.json",
    "SOURCE_HASHES.json", "DIRTY_TREE_MANIFEST.json",
    "INTEGRITY_BEFORE.json", "INTEGRITY_AFTER.json",
    "RESOURCE_PREFLIGHT.json", "PROVENANCE.json", "COMMANDS.txt",
    "REPORT.md", "SHA256SUMS",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def metadata_split(seed: int = SEED) -> dict[str, Any]:
    """Label-free, deterministic, mirror-atomic phase-stratified split."""

    generator = random.Random(int(seed))
    train: list[str] = []
    validation: list[str] = []
    proof: dict[str, Any] = {}
    for phase, frozen_units in CAPTURE_PHASE_UNITS.items():
        units = [tuple(unit) for unit in frozen_units]
        generator.shuffle(units)
        validation_count = 2
        validation_units = units[:validation_count]
        train_units = units[validation_count:]
        phase_train = sorted(action for unit in train_units for action in unit)
        phase_validation = sorted(
            action for unit in validation_units for action in unit
        )
        train.extend(phase_train)
        validation.extend(phase_validation)
        proof[phase] = {
            "train_actions": phase_train,
            "validation_actions": phase_validation,
            "train_atomic_units": [list(unit) for unit in train_units],
            "validation_atomic_units": [list(unit) for unit in validation_units],
        }
    payload = {
        "seed": int(seed),
        "algorithm": (
            "Python random.Random(seed); independently shuffle frozen atomic "
            "units in early/mid/late; first two units per phase validation; rest train"
        ),
        "uses_labels_or_ranges": False,
        "canonical_actions": list(CANONICAL_ACTIONS),
        "unavailable_actions": list(UNAVAILABLE_ACTIONS),
        "mirror_pairs_atomic": [list(pair) for pair in MIRROR_PAIRS],
        "train_actions": sorted(train),
        "validation_actions": sorted(validation),
        "capture_phase_proof": proof,
        "locked_external_reuse_zero_evaluation": ["H01", "H02"],
    }
    combined = sorted(payload["train_actions"] + payload["validation_actions"])
    if combined != sorted(CANONICAL_ACTIONS):
        raise RuntimeError("metadata split does not partition canonical actions")
    if set(payload["train_actions"]) & set(payload["validation_actions"]):
        raise RuntimeError("metadata split overlaps")
    for left, right in MIRROR_PAIRS:
        if ((left in payload["train_actions"]) !=
                (right in payload["train_actions"])):
            raise RuntimeError("mirror pair was split")
    return payload


def _inventory_digest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False}
    digest = hashlib.sha256()
    count = 0
    total = 0
    for base, directories, files in os.walk(path):
        directories.sort()
        files.sort()
        for filename in files:
            item = Path(base) / filename
            stat = item.stat()
            relative = item.relative_to(ROOT).as_posix()
            digest.update(
                f"{relative}\0{stat.st_size}\0{stat.st_mtime_ns}\n".encode()
            )
            count += 1
            total += stat.st_size
    return {
        "exists": True,
        "file_count": count,
        "total_bytes": total,
        "path_size_mtime_inventory_sha256": digest.hexdigest(),
        "contents_decoded": False,
    }


def _integrity_snapshot() -> dict[str, Any]:
    selected = {}
    for relative in PREEXISTING_INTEGRITY_PATHS:
        path = ROOT / relative
        selected[relative] = {
            "exists": path.is_file(),
            "sha256": _sha256(path) if path.is_file() else None,
        }
    return {
        "preexisting_o0_files": selected,
        "excluded_paths_metadata_only": {
            relative: _inventory_digest(ROOT / relative)
            for relative in EXCLUDED_PATHS
        },
    }


def _model_contract() -> dict[str, Any]:
    return {
        "phase": "O1A",
        "status": "PREREGISTERED_NO_DATA",
        "purpose": "diagnostic low-dimensional skeleton-conditioned RF shadow field",
        "not_production_solver": True,
        "manual_anthropometry_required": False,
        "feature_owner": "strict-prior native-200 skeleton and pre-epoch tag origin",
        "label_owner": "same-node held-link LOO after feature freeze",
        "current_epoch_ranges_used_by_features": False,
        "numerics": {
            "integration_method": INTEGRATION_METHOD,
            "gauss_order_per_partition": INTEGRATION_GAUSS_ORDER_PER_PARTITION,
            "minimum_width_support_sigma": INTEGRATION_MINIMUM_WIDTH_SUPPORT_SIGMA,
            "reference_absolute_tolerance": INTEGRATION_REFERENCE_ABSOLUTE_TOLERANCE,
            "reference_relative_tolerance": INTEGRATION_REFERENCE_RELATIVE_TOLERANCE,
            "maximum_compiled_nodes": INTEGRATION_MAXIMUM_NODES,
            "longitudinal_sigmoid_sharpness": LONGITUDINAL_SIGMOID_SHARPNESS,
            "constants_tunable": False,
        },
        "equations": {
            "ray": "x(s)=tag_origin+s*(anchor-tag_origin), s in [0,1]",
            "segment": (
                "L=||b-a||, t=((x-a) dot (b-a)/L)/L, "
                "r=||(x-a)-((x-a) dot u)u||, "
                "f=exp(-0.5*(r/(scale*L))^2)*sigmoid(k*t)*sigmoid(k*(1-t))"
            ),
            "torso": (
                "same longitudinal taper; anisotropic Gaussian in the torso "
                "lateral/AP frame with widths lateral_scale*mean(shoulder,hip) "
                "and ap_scale*mean(shoulder,hip)"
            ),
            "class_union": "F_class=1-product_j(1-f_j), with each f_j in [0,1]",
            "integration": "adaptive bounded integration of F_class(x(s)) over s in [0,1]",
            "B0": "mu=nuisance(beta)",
            "B1": "mu=B0+torso_opacity_m*torso_exposure",
            "nonadditive_partition": (
                "A=F_arm*(1-0.5*F_leg), L=F_leg*(1-0.5*F_arm); "
                "arm_increment=(1-F_torso)*A; leg_increment=(1-F_torso)*L; "
                "F_torso+arm_increment+leg_increment=1-product_classes(1-F_class)"
            ),
            "B2": "mu=B1+arm_opacity_m*arm_increment+leg_opacity_m*leg_increment",
            "no_interaction": "no facing-by-shadow or large-by-small interaction",
        },
        "morphology_parameter_order": list(MORPHOLOGY_PARAMETER_NAMES),
        "morphology_dimension": len(MORPHOLOGY_PARAMETER_NAMES),
        "morphology_bounds": {
            name: list(MORPHOLOGY_BOUNDS[name])
            for name in MORPHOLOGY_PARAMETER_NAMES
        },
        "morphology_constraints": {
            "side_symmetric_global_only": True,
            "scale_strictly_positive_finite": True,
            "opacity_nonnegative_finite_bounded": True,
            "per_node_anchor_action_link_morphology": False,
            "degenerate_segment": "FAIL_CLOSED",
        },
        "large_field_landmarks": [
            "pelvis_center", "hip_left", "hip_right", "shoulder_mid",
            "shoulder_left", "shoulder_right",
        ],
        "small_field_segments": {
            "arms": ["upper_arm_left", "forearm_left", "upper_arm_right", "forearm_right"],
            "legs": ["thigh_left", "shank_left", "thigh_right", "shank_right"],
        },
        "forbidden_body_parts": ["head", "hands", "feet"],
        "emitter_local_segments": EMITTER_LOCAL_SEGMENTS,
        "emitter_local_policy": {
            "limb_emitter": "exclude its named local limb segment",
            "torso_or_pelvis_emitter": "large torso field UNOBSERVABLE_LOCAL and excluded",
            "local_ambiguity_is_not_a_shadow_feature": True,
        },
        "nuisance": {
            "dimension": len(NUISANCE_NAMES),
            "names": list(NUISANCE_NAMES),
            "identifiability": (
                "intercept plus 9 node and 7 anchor reference-free sum-zero contrasts"
            ),
            "same_basis_byte_identical_B0_B1_B2": True,
            "action_or_time_intercept": False,
            "adaptive_spatial_basis": False,
        },
        "likelihood": {
            "signed_innovation": "measured_range_m-minus-held_link_LOO_predicted_range_m",
            "density": (
                "(1-pi)*Normal(y|mu,sigma) + "
                "pi*ExGaussianPositiveTail(y|mu,sigma,tau)"
            ),
            "parameters": "common nuisance beta, log_sigma, log_tau, logit_pi, nested morphology",
            "bounds": {
                "beta_m": [-2.0, 2.0], "log_sigma_m": [-5.2983173665, 0.6931471806],
                "log_tau_m": [-5.2983173665, 1.0986122887], "logit_pi": [-6.0, 3.0],
            },
            "optimizer": {
                "name": "scipy.optimize.minimize L-BFGS-B", "single_start": True,
                "maximum_iterations": 500, "ftol": 1e-10, "gtol": 1e-7,
                "architecture_or_parameter_sweep": False,
            },
            "regularization": (
                "0.5e-3*sum((beta/0.5m)^2)+0.5e-2*sum(log(scale/geometric_bound_center)^2)"
                "+0.5e-2*sum((opacity/1m)^2)"
            ),
        },
        "radio_channel": "UNKNOWN_NOT_USED_NO_FRESNEL_NUMERIC_FEATURE",
    }


def _eligibility_contract() -> dict[str, Any]:
    return {
        "feature_freeze_precedes_label": True,
        "feature_pose_time_rule": "strict pose_time < per-link measurement time",
        "feature_tag_origin": "pre-epoch causal origin independent of all current-epoch ranges",
        "label": "same-node held-link LOO signed innovation",
        "target_link_exactly_omitted": True,
        "duplicate_node_anchor_observation": "INELIGIBLE",
        "minimum_unique_remaining_anchors": 4,
        "minimum_geometry_rank": 3,
        "maximum_geometry_condition": 1e8,
        "ineligible_rows": "reported separately and never used in fitting/evaluation",
        "eligible_optimizer_failure": "HARD_FAIL",
        "H01_H02_policy": "LOCKED_EXTERNAL_REUSE_PREVIOUSLY_INSPECTED_ZERO_EVALUATION_O1A",
    }


def _eval_contract() -> dict[str, Any]:
    return {
        "paired_rows": "one frozen eligible-row intersection shared by B0/B1/B2",
        "primary_metric": "held-out predictive log-score per eligible link row",
        "bootstrap": {
            "unit": "action then epoch cluster", "replicates": 2000,
            "seed": SEED + 1, "interval": "percentile 95%",
        },
        "promotion_gates": {
            "B1_minus_B0_logscore_lower95_gt_0": True,
            "B2_minus_B1_logscore_lower95_gt_0": True,
            "validation_action_sign_reversal_allowed": False,
            "calibration_ece_nonregression_each_nesting_step": True,
            "positive_tail_logscore_strict_improvement_each_step": True,
            "information_rank": 3,
            "information_condition_cap": 1e8,
            "information_condition_nonregression_vs_B0": True,
        },
        "calibration": {
            "bins": "10 fixed equal-count bins fitted from train predictions and applied unchanged",
            "target": "positive-tail posterior probability and positive innovation occurrence",
        },
        "information_weight": (
            "w_i=1/(sigma^2+pi_i*(tau+shadow_shift_i)^2), strictly positive; "
            "H=sum_i w_i*j_i*j_i^T; compare trace-normalized H/trace(H)"
        ),
        "no_best_radius_or_model_selection": True,
        "no_data_evaluated_in_O1A": True,
    }


def _model_markdown(contract: dict[str, Any]) -> str:
    return f"""# Phase O1A frozen model contract

This is a preregistered fixture-stage diagnostic model. It is not connected to
the production solver and no UWB/HXX observation is opened in O1A.

The causal ray is `x(s)=o+s(a-o)`. Each finite body segment uses a Gaussian
transverse field scaled by that segment's skeleton length and the fixed smooth
longitudinal taper `sigmoid({contract['numerics']['longitudinal_sigmoid_sharpness']}t) *
sigmoid({contract['numerics']['longitudinal_sigmoid_sharpness']}(1-t))`. The
integral uses frozen geometry-adaptive partitioned Gauss-Legendre integration
with {contract['numerics']['gauss_order_per_partition']} nodes per partition,
minimum-width support of {contract['numerics']['minimum_width_support_sigma']}
sigma, and bounded work. A class is the
bounded union `1-product(1-f_j)`, not summed chord
length. Large torso and small articulated exposures stay separate.

The seven global side-symmetric parameters are `{', '.join(MORPHOLOGY_PARAMETER_NAMES)}`.
No manual limb width, per-person circumference, per-node morphology, action
intercept, facing interaction, head, hand, or foot feature is admitted.

B0 is the frozen common nuisance model. B1 adds only torso exposure. B2 adds
only arm and leg exposure to B1. Zero opacity makes each larger model reproduce
its immediate parent exactly. Labels, when a later phase is separately
authorized, must be same-node held-link LOO innovations and may be constructed
only after strict-prior features are frozen.
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    output = arguments.output.resolve()
    if output.exists():
        raise FileExistsError(output)
    if output.parent != (ROOT / "logs").resolve():
        raise ValueError("O1A evidence must be a direct timestamped logs child")

    started = time.monotonic()
    projected_bytes = 1_000_000
    if projected_bytes > 100_000_000:
        raise RuntimeError("projected evidence exceeds O1A 100 MB cap")
    nrf_free = shutil.disk_usage("/mnt/nrf_ssd").free
    root_free = shutil.disk_usage("/").free
    if nrf_free < 100_000_000_000 or root_free < 40_000_000_000:
        raise RuntimeError("Fusion workspace free-space gate failed")

    before = _integrity_snapshot()
    output.mkdir(parents=False)
    model = _model_contract()
    split = metadata_split()
    eligibility = _eligibility_contract()
    evaluation = _eval_contract()
    allowlist = {
        "new_or_phase_owned_files": [
            "src/biospur_fusion/c2_uwb_calibration/rf_shadow_field.py",
            "tests/test_c2_rf_shadow_field.py",
            "tools/preregister_c2_rf_shadow_field_o1a.py",
            *[f"{output.relative_to(ROOT).as_posix()}/{name}" for name in EVIDENCE_FILES],
        ],
        "modification_outside_allowlist": "FORBIDDEN",
        "production_solver_IK_contact_viewer_frozen_input_changes": False,
    }
    source_hashes = {
        relative: _sha256(ROOT / relative) for relative in SOURCE_OWNER_PATHS
    }
    dirty = {
        "baseline_commit_supplied_by_director_not_queried_with_git": BASELINE_COMMIT,
        "git_command_used": False,
        "preexisting_dirty_work_preserved": True,
        "known_preexisting_phase_O0_files": list(PREEXISTING_INTEGRITY_PATHS),
        "O1A_phase_owned_candidates": allowlist["new_or_phase_owned_files"][:3],
        "working_tree_claim": "no claim that unrelated working tree is clean",
    }
    _json(output / "MODEL_CONTRACT.json", model)
    (output / "MODEL_CONTRACT.md").write_text(_model_markdown(model))
    _json(output / "SPLIT.json", split)
    _json(output / "ELIGIBILITY.json", eligibility)
    _json(output / "EVAL_CONTRACT.json", evaluation)
    _json(output / "FILE_ALLOWLIST.json", allowlist)
    _json(output / "SOURCE_HASHES.json", source_hashes)
    _json(output / "DIRTY_TREE_MANIFEST.json", dirty)
    _json(output / "INTEGRITY_BEFORE.json", before)
    commands = (
        "PYTHONPATH=src:tools pytest -q tests/test_c2_rf_shadow_field.py\n"
        f"PYTHONPATH=src {Path(__file__).relative_to(ROOT)} --output {output.relative_to(ROOT)}\n"
    )
    (output / "COMMANDS.txt").write_text(commands)

    after = _integrity_snapshot()
    if before != after:
        _json(output / "INTEGRITY_AFTER.json", after)
        raise RuntimeError("preexisting/excluded path integrity changed during O1A")
    _json(output / "INTEGRITY_AFTER.json", after)
    elapsed = time.monotonic() - started
    usage = resource.getrusage(resource.RUSAGE_SELF)
    resource_record = {
        "wall_seconds": elapsed,
        "maximum_rss_kib": usage.ru_maxrss,
        "ram_cap_bytes": 1_500_000_000,
        "evidence_projected_bytes": projected_bytes,
        "evidence_cap_bytes": 100_000_000,
        "gpu_used": False,
        "real_data_read_or_decoded": False,
        "raw_UWB_or_HXX_opened": False,
    }
    _json(output / "RESOURCE_PREFLIGHT.json", resource_record)
    provenance = {
        "phase": "O1A", "status": "PREFLIGHT_COMPLETE_NO_DATA",
        "baseline_commit": BASELINE_COMMIT, "seed": SEED,
        "python": sys.version, "pid": os.getpid(),
        "real_data_evaluation_count": 0,
        "locked_external_reuse_subjects": ["H01", "H02"],
        "corrective_reruns": 0,
    }
    _json(output / "PROVENANCE.json", provenance)
    report = f"""# Phase O1A preflight report

Status: `PREFLIGHT_COMPLETE_NO_DATA`.

The isolated seven-parameter skeleton-conditioned field and all model, split,
eligibility, fitting, and evaluation contracts were frozen before any UWB/HXX
observation access. H01 and H02 are locked external reuse subjects and have
zero O1A evaluation rows. No production solver, IK, contact, viewer, frozen
input, threshold, or previously sealed O0 file was modified by this preflight.

The morphology is inferred in a later separately authorized phase; it accepts
no manually measured limb width or circumference. Large torso and small limb
fields remain separate. Emitter-local limbs are excluded, and torso/pelvis
emitters mark the large field unobservable rather than reconstructing own-facing.

Metadata split: train `{','.join(split['train_actions'])}`; validation
`{','.join(split['validation_actions'])}`. Action 01 is explicitly unavailable.
Every mirror pair is atomic and both partitions cover early/mid/late capture
phases. The split uses only frozen action identifiers and seed {SEED}.

Preflight wall time: {elapsed:.6f} s. Peak RSS: {usage.ru_maxrss} KiB.
"""
    (output / "REPORT.md").write_text(report)

    actual_bytes = sum(
        path.stat().st_size for path in output.iterdir() if path.is_file()
    )
    if actual_bytes > 100_000_000 or usage.ru_maxrss * 1024 > 1_500_000_000:
        raise RuntimeError("actual O1A resource cap failed")
    # The seal excludes itself and is written last.
    seal_lines = []
    for path in sorted(output.iterdir()):
        if path.is_file() and path.name != "SHA256SUMS":
            seal_lines.append(f"{_sha256(path)}  {path.name}")
    (output / "SHA256SUMS").write_text("\n".join(seal_lines) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
