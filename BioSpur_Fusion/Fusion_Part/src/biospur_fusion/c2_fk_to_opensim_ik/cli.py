"""Bounded subprocess entry points for FK-to-OpenSim IK evidence generation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from biospur_fusion.c2_3a_kinematics import load_frozen_c2_3a, load_frozen_c2_hxx_diagnostics

from .adapter import PILOT_EPISODES, build_model, run_episode, run_identity
from .render import render_and_summarize, validate_full_outputs, write_interactive_viewer


def _episode(frozen_primary, frozen_hxx, key):
    return frozen_hxx.episodes[key] if key.startswith("H") else frozen_primary.episodes[key]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("build", "identity", "episode", "render", "full", "viewer", "validate"))
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--label")
    args = parser.parse_args()
    frozen = load_frozen_c2_3a(workspace=args.workspace)
    model_path = args.evidence / "model" / "c2_frozen_frame_model.osim"
    if args.command == "build":
        result = build_model(frozen, model_path)
    elif args.command == "identity":
        result = run_identity(model_path, args.evidence / "identity")
    elif args.command in ("full", "viewer", "validate"):
        hxx = load_frozen_c2_hxx_diagnostics(workspace=args.workspace)
        episode_items = [
            (f"primary_{key}", episode)
            for key, episode in sorted(frozen.episodes.items())
        ] + [
            (key, episode)
            for key, episode in sorted(hxx.episodes.items())
        ]
        if args.command == "viewer":
            result = write_interactive_viewer(
                frozen,
                episode_items,
                model_path,
                args.evidence / "all_19plus2",
                args.evidence / "viewer" / "c2_fk_vs_opensim_ik.html",
            )
            (args.evidence / "viewer" / "VIEWER_RESULT.json").write_text(
                json.dumps(result, indent=2, sort_keys=True) + "\n"
            )
        elif args.command == "validate":
            result = validate_full_outputs(
                frozen,
                episode_items,
                model_path,
                args.evidence / "all_19plus2",
            )
            (args.evidence / "all_19plus2" / "FULL_VALIDATION.json").write_text(
                json.dumps(result, indent=2, sort_keys=True) + "\n"
            )
        else:
            results = {
                label: run_episode(
                    episode,
                    model_path,
                    args.evidence / "all_19plus2" / label,
                )
                for label, episode in episode_items
            }
            result = {
                "full_19plus2": len(results) == 21,
                "episode_count": len(results),
                "all_finite": all(item["finite"] for item in results.values()),
                "episodes": results,
            }
            (args.evidence / "all_19plus2" / "FULL_RESULT.json").write_text(
                json.dumps(result, indent=2, sort_keys=True) + "\n"
            )
    else:
        if args.label not in PILOT_EPISODES:
            raise ValueError("unknown pilot label")
        key = PILOT_EPISODES[args.label]
        hxx = load_frozen_c2_hxx_diagnostics(workspace=args.workspace) if key.startswith("H") else None
        episode = _episode(frozen, hxx, key)
        episode_root = args.evidence / "episodes" / args.label
        if args.command == "episode":
            result = run_episode(episode, model_path, episode_root)
        else:
            result = render_and_summarize(
                frozen,
                episode,
                args.label,
                model_path,
                episode_root / "official" / "official_ik.sto",
                args.evidence / "rendering",
            )
            (episode_root / "RENDER_RESULT.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
