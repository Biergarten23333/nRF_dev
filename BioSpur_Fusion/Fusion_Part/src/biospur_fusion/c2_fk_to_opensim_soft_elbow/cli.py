"""Killable stage commands for the bounded soft-elbow pilot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from biospur_fusion.c2_3a_kinematics import load_frozen_c2_3a
from biospur_fusion.c2_fk_to_opensim_ik.render import render_and_summarize

from .soft_elbow import (
    OUT_OF_PLANE_SIGMA_RAD,
    OUT_OF_PLANE_WEIGHT,
    analyze_episode,
    build_candidate_model,
    run_episode,
    run_signed_fixture,
)


EPISODES = {"02": "01", "06": "06", "07": "07"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "fixture", "episode", "render"))
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--label", choices=tuple(EPISODES))
    parser.add_argument("--attempt", default="fixture_attempt_002")
    args = parser.parse_args()
    frozen = load_frozen_c2_3a(workspace=args.workspace)
    model_path = args.evidence / "model" / "c2_frozen_frame_soft_elbow.osim"
    if args.command == "prepare":
        prior = args.workspace / "logs/c2_fk_to_scaled_opensense_radioulnar_20260903_013140"
        result = {
            "schema": "biospur.c2_fk_to_opensim_soft_elbow.run_contract.v1",
            "scope": "02/06/07 official OpenSim soft-elbow pilot only",
            "supersedes": "only the terminality inference in the prior intermediate closeout",
            "preserves_prior_measurements_and_rejections": True,
            "prior_final_report": str((prior / "FINAL_REPORT.md").resolve()),
            "prior_final_manifest": str((prior / "FINAL_MANIFEST.json").resolve()),
            "out_of_plane_sigma_rad": OUT_OF_PLANE_SIGMA_RAD,
            "out_of_plane_weight": OUT_OF_PLANE_WEIGHT,
            "uncertainty_owner": str((prior / "CLEAN_FLEXION_QMT_OLSSON_AUDIT.json").resolve()),
            "uncertainty_rule": "maximum parent-axis leave-one-block-out deviation across clean 06/07 fits",
            "no_hard_functional_axis": True,
            "no_custom_optimizer_residual_jacobian": True,
            "no_uwb_raw_qmt_rerun": True,
            "no_action_semantics_in_objective": True,
            "probe_wall_limit_s": 120,
            "model": build_candidate_model(frozen, model_path),
        }
        (args.evidence / "RUN_CONTRACT.json").write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    elif args.command == "fixture":
        result = run_signed_fixture(model_path, args.evidence / args.attempt)
    else:
        if args.label is None:
            raise ValueError("--label is required")
        episode = frozen.episodes[EPISODES[args.label]]
        episode_dir = args.evidence / "episodes" / args.label
        if args.command == "episode":
            result = run_episode(frozen, episode, model_path, episode_dir)
        else:
            result = render_and_summarize(
                frozen,
                episode,
                args.label,
                model_path,
                episode_dir / "official" / "official_ik.sto",
                args.evidence / "rendering",
            )
            result["analysis"] = analyze_episode(
                frozen,
                episode,
                model_path,
                episode_dir / "official" / "official_ik.sto",
            )
            (episode_dir / "RENDER_RESULT.json").write_text(
                json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
