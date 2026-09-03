"""Bounded commands for the Rajagopal soft-elbow candidate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from biospur_fusion.c2_3a_kinematics import load_frozen_c2_3a
from biospur_fusion.c2_fk_to_scaled_opensense.functional_run import (
    DISPLAY_LABEL,
    analyze_episode,
)

from .candidate import (
    CONSTRAINT_WEIGHT,
    ELBOW_FRAME_BASIS,
    ORIENTATION_WEIGHT,
    OUT_OF_PLANE_WEIGHT,
    SOURCE_MODEL_SHA256,
    configure_model,
    run_episode,
    run_episode_probe,
    run_fixture,
)
from .sensitivity import run_weight_sensitivity
from .proxy_render import analyze_and_render_dual_proxy


SOURCE_MODEL = Path(
    "logs/c2_fk_to_scaled_opensense_radioulnar_20260903_013140/"
    "radius_attempt_001/model/calibrated_tpose_ground_gauge_radius.osim"
)
EPISODES = {"02": "01", "06": "06", "07": "07"}
FIXTURE_ANCHOR_MODEL = Path(
    "logs/c2_fk_to_rajagopal_soft_elbow_20260903_033000/"
    "model/rajagopal_soft_elbow.osim"
)
FIXTURE_ANCHOR_INPUT = Path(
    "logs/c2_fk_to_rajagopal_soft_elbow_20260903_033000/"
    "fixture/model_generated_orientations.sto"
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=(
            "prepare",
            "fixture",
            "sensitivity",
            "probe02",
            "episode",
            "analyze",
            "proxy",
        ),
    )
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--label", choices=tuple(EPISODES))
    parser.add_argument("--model", type=Path)
    parser.add_argument("--oop-weight", type=float, choices=(0.0, 0.001))
    args = parser.parse_args()
    source_model = args.workspace / SOURCE_MODEL
    generated_model_path = args.evidence / "model" / "rajagopal_soft_elbow.osim"
    model_path = (
        args.model if args.model and args.model.is_absolute()
        else args.workspace / args.model if args.model
        else generated_model_path
    )
    out_of_plane_weight = (
        OUT_OF_PLANE_WEIGHT if args.oop_weight is None else args.oop_weight
    )
    if args.command == "prepare":
        result = {
            "schema": "biospur.c2.rajagopal_soft_elbow.run_contract.v1",
            "scope": "model fixture, then 02/06/07 only",
            "source_model_sha256": SOURCE_MODEL_SHA256,
            "source_model_owner": "preserved visually rejected diagnostic radius attempt; only its corrected-02 placement, rigid radius IMU reparent, and official unlocked pro_sup mechanism are inherited; the rejected centered axial-zero model is not imported",
            "elbow_frame_basis": ELBOW_FRAME_BASIS.tolist(),
            "out_of_plane_weight": OUT_OF_PLANE_WEIGHT,
            "out_of_plane_range": "official UniversalJoint native default chart, unclamped",
            "out_of_plane_weight_owner": "conservative engineering-only weak ratio 0.001 preserving about 98% of the fixed noiseless OOP truth; no real-data, covariance, or biomechanical-optimality owner",
            "orientation_weight": ORIENTATION_WEIGHT,
            "constraint_weight": "Infinity (official InverseKinematicsSolver exact-constraint behavior)",
            "no_result_selection_or_grid": True,
            "no_custom_optimizer_residual_jacobian": True,
            "no_qmt_rerun_raw_uwb": True,
            "probe_wall_limit_s": 120,
            "model": configure_model(
                source_model, generated_model_path, args.evidence / "model"
            ),
        }
        (args.evidence / "RUN_CONTRACT.json").write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    elif args.command == "fixture":
        result = run_fixture(model_path, args.evidence / "fixture")
    elif args.command == "sensitivity":
        result = run_weight_sensitivity(
            args.workspace / FIXTURE_ANCHOR_MODEL,
            args.workspace / FIXTURE_ANCHOR_INPUT,
            args.evidence,
        )
    elif args.command == "probe02":
        frozen = load_frozen_c2_3a(workspace=args.workspace)
        result = run_episode_probe(
            frozen.episodes[EPISODES["02"]],
            model_path,
            args.evidence / "lifecycle_probe_02_rows_0_350_700",
            (0, 350, 700),
            out_of_plane_weight=out_of_plane_weight,
        )
    elif args.command == "proxy":
        if args.label is None:
            raise ValueError("--label is required")
        result = analyze_and_render_dual_proxy(
            args.workspace, model_path, args.evidence, args.label
        )
    else:
        if args.label is None:
            raise ValueError("--label is required")
        frozen = load_frozen_c2_3a(workspace=args.workspace)
        key = EPISODES[args.label]
        episode = frozen.episodes[key]
        if args.command == "episode":
            result = run_episode(
                episode,
                model_path,
                args.evidence / "episodes" / DISPLAY_LABEL[key],
                out_of_plane_weight=out_of_plane_weight,
                requested_protocol_label=args.label,
                display_label=DISPLAY_LABEL[key],
            )
        else:
            result = analyze_episode(
                args.workspace, model_path, args.evidence, key
            )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
