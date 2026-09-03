"""Small child-process entrypoints for the bounded soft-biomechanics stage."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from biospur_fusion.c2_3a_kinematics import (
    load_frozen_c2_3a,
    load_frozen_c2_hxx_diagnostics,
)

from .pipeline import (
    BASELINE_MODEL,
    _json_write,
    estimate_capture_wide_ownership,
    prepare_candidate_model,
    run_episode,
    run_mechanism_fixture,
)
from .report import analyze_episode


PILOT = {
    "00_initial_still": ("00", "primary_00"),
    "02_t_pose": ("01", "primary_01"),
    "06_elbow_left": ("06", "primary_06"),
    "07_elbow_right": ("07", "primary_07"),
    "10_knee_left": ("10", "primary_10"),
    "11_knee_right": ("11", "primary_11"),
}


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--workspace", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    episode = subparsers.add_parser("episode")
    episode.add_argument("--workspace", type=Path, required=True)
    episode.add_argument("--model", type=Path, required=True)
    episode.add_argument("--ownership", type=Path, required=True)
    episode.add_argument("--episode", required=True)
    episode.add_argument("--profile", choices=("weak", "central", "strong"), required=True)
    episode.add_argument("--output", type=Path, required=True)
    fixture = subparsers.add_parser("fixture")
    fixture.add_argument("--model", type=Path, required=True)
    fixture.add_argument("--ownership", type=Path, required=True)
    fixture.add_argument("--output", type=Path, required=True)
    analyze = subparsers.add_parser("analyze-pilot")
    analyze.add_argument("--workspace", type=Path, required=True)
    analyze.add_argument("--model", type=Path, required=True)
    analyze.add_argument("--ownership", type=Path, required=True)
    analyze.add_argument("--pilot-root", type=Path, required=True)
    analyze.add_argument("--output", type=Path, required=True)
    analyze_full = subparsers.add_parser("analyze-full")
    analyze_full.add_argument("--workspace", type=Path, required=True)
    analyze_full.add_argument("--model", type=Path, required=True)
    analyze_full.add_argument("--ownership", type=Path, required=True)
    analyze_full.add_argument("--full-root", type=Path, required=True)
    analyze_full.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    frozen = load_frozen_c2_3a()
    if args.command == "prepare":
        ownership = estimate_capture_wide_ownership(frozen)
        _json_write(args.output / "CAPTURE_WIDE_OWNERSHIP.json", ownership)
        model = prepare_candidate_model(
            args.workspace,
            ownership,
            args.output / "model/c2_soft_chart_model.osim",
        )
        _json_write(args.output / "MODEL_MANIFEST.json", model)
        result = {"ownership": ownership, "model": model}
    elif args.command == "episode":
        ownership = json.loads(args.ownership.read_text(encoding="utf-8"))
        if args.episode.startswith("H"):
            holdouts = load_frozen_c2_hxx_diagnostics(workspace=args.workspace)
            if args.episode not in holdouts.episodes:
                raise KeyError(f"unknown frozen holdout key: {args.episode}")
            episode_data = holdouts.episodes[args.episode]
        else:
            if args.episode not in frozen.episodes:
                raise KeyError(f"unknown frozen primary key: {args.episode}")
            episode_data = frozen.episodes[args.episode]
        result = run_episode(args.model, episode_data, ownership, args.profile, args.output)
    elif args.command == "fixture":
        ownership = json.loads(args.ownership.read_text(encoding="utf-8"))
        result = run_mechanism_fixture(args.model, ownership, args.output)
    elif args.command == "analyze-pilot":
        ownership = json.loads(args.ownership.read_text(encoding="utf-8"))
        results = {}
        for label, (key, sealed_label) in PILOT.items():
            results[label] = analyze_episode(
                frozen,
                frozen.episodes[key],
                label,
                ownership,
                args.workspace / BASELINE_MODEL,
                args.workspace
                / "logs/c2_fk_to_opensim_ik_20260902_235206/all_19plus2"
                / sealed_label
                / "official/official_ik.sto",
                args.model,
                {profile: args.pilot_root / label / profile for profile in ("weak", "central", "strong")},
                args.output / "rendering",
            )
        result = {
            "schema": "c2-soft-biomechanics-fixed-pilot-v1",
            "episodes": results,
            "solver_call_count": 18,
            "all_numeric_pass": all(row["numeric_pass"] for row in results.values()),
            "pixel_inspection_pending": True,
            "full_19plus2_eligible": False,
        }
        _json_write(args.output / "PILOT_ANALYSIS.json", result)
    else:
        ownership = json.loads(args.ownership.read_text(encoding="utf-8"))
        holdouts = load_frozen_c2_hxx_diagnostics(workspace=args.workspace)
        episode_items = [
            (f"primary_{key}", episode, False)
            for key, episode in sorted(frozen.episodes.items())
        ] + [
            (key, episode, True)
            for key, episode in sorted(holdouts.episodes.items())
        ]
        results = {}
        for label, episode_data, holdout in episode_items:
            key = episode_data.key
            row = analyze_episode(
                frozen,
                episode_data,
                label,
                ownership,
                args.workspace / BASELINE_MODEL,
                args.workspace
                / "logs/c2_fk_to_opensim_ik_20260902_235206/all_19plus2"
                / label
                / "official/official_ik.sto",
                args.model,
                {"central": args.full_root / key / "central"},
                args.output / "full_rendering_not_generated",
                render=False,
            )
            row["holdout"] = holdout
            row["calibration_refit"] = False
            results[label] = row
        result = {
            "schema": "c2-soft-biomechanics-full-19plus2-analysis-v1",
            "episode_count": len(results),
            "total_rows": sum(row["rows"] for row in results.values()),
            "primary_count": sum(not row["holdout"] for row in results.values()),
            "holdout_count": sum(row["holdout"] for row in results.values()),
            "profile": "central",
            "capture_wide_model_and_parameters_unchanged": True,
            "h01_h02_calibration_refit": False,
            "all_numeric_pass": all(row["numeric_pass"] for row in results.values()),
            "all_physical_gates_pass": all(row["physical_gates"]["passed"] for row in results.values()),
            "episodes": results,
        }
        _json_write(args.output / "FULL_19PLUS2_ANALYSIS.json", result)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
