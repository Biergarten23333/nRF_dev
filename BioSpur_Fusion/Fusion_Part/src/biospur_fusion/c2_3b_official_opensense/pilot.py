"""Run the bounded 00/02 official OpenSense pilot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from biospur_fusion.c2_3a_kinematics import load_frozen_c2_3a

from .adapter import (
    configure_official_model,
    configure_opensim_log,
    run_official_imu_ik,
    sha256_file,
    write_frozen_orientation_sto,
)


PILOT_EPISODES = {
    "00_initial_still": "00",
    "02_t_pose": "01",
}


def run_pilot(workspace: Path, evidence_root: Path, official_model: Path) -> dict:
    workspace = workspace.resolve()
    evidence_root = evidence_root.resolve()
    output_root = evidence_root / "real_pilot_00_02"
    output_root.mkdir(parents=True, exist_ok=True)
    configure_opensim_log(output_root / "model_configuration_opensim.log")

    frozen = load_frozen_c2_3a(workspace=workspace)
    configured_model = output_root / "model" / "c2_official_configured.osim"
    model_manifest = configure_official_model(official_model, configured_model)
    (output_root / "model" / "model_manifest.json").write_text(
        json.dumps(model_manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    episodes: dict[str, object] = {}
    for capture_label, frozen_key in PILOT_EPISODES.items():
        episode = frozen.episodes[frozen_key]
        episode_dir = output_root / capture_label
        orientations_path = episode_dir / "input" / "frozen_orientations.sto"
        input_manifest = write_frozen_orientation_sto(episode, orientations_path)
        (episode_dir / "input" / "input_manifest.json").write_text(
            json.dumps(input_manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        run_manifest = run_official_imu_ik(
            configured_model,
            orientations_path,
            episode_dir / "official_output",
            first_time_s=float(input_manifest["first_elapsed_time_s"]),
            last_time_s=float(input_manifest["last_elapsed_time_s"]),
        )
        episodes[capture_label] = {
            "frozen_episode": frozen_key,
            "input": input_manifest,
            "official_run": run_manifest,
        }

    result = {
        "schema": "biospur-c2-3b-official-opensense-pilot-v1",
        "scientific_pass": False,
        "frozen_seal_sha256": frozen.verification.seal_sha256,
        "frozen_manifest_sha256": frozen.verification.manifest_sha256,
        "model": model_manifest,
        "episodes": episodes,
        "uwb_consumed": False,
        "per_action_calibration": False,
        "frozen_quaternion_rewrite": False,
        "custom_optimizer_or_residual": False,
    }
    manifest_path = output_root / "PILOT_MANIFEST.json"
    manifest_path.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    result["pilot_manifest_sha256"] = sha256_file(manifest_path)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--official-model", type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            run_pilot(args.workspace, args.evidence_root, args.official_model),
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
