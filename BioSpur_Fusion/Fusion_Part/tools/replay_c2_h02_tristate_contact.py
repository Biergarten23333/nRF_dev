#!/usr/bin/env python3
"""Read-only H02 replay of the three-state ankle support detector.

This diagnostic deliberately does not run UWB/root fusion.  It reuses a
sealed H02 root trajectory only to express analytic-base ankle height in the
world frame.  Classification kinematics come exclusively from the accepted
native-200 analytic pose, never from the articulated UWB correction.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np

from biospur_fusion.c2_3a_kinematics import (
    load_frozen_c2_3a,
    load_frozen_c2_hxx_diagnostics,
)
from biospur_fusion.c2_uwb_calibration.frozen_body_proxy import (
    FrozenHoldoutBodyProxy,
    frozen_world_alignment,
)
from biospur_fusion.c2_uwb_root_world.ankle_contact import (
    AnkleContactConfig,
    AnkleContactDetector,
    FootContactEvidence,
    FootSupportState,
)

from run_c2_h01_shared_root_imu_fusion import (
    ANKLE_NODE_TO_SIDE,
    DEFAULT_CLOCK,
    PELVIS_NODE,
    _ankle_imu_rows,
    _beacon_boundary_bridges,
    _clock_models,
    _fit_contact_profiles,
    _load_analytic_pose_owner,
    _load_holdout,
    _pelvis_imu,
    _sha256,
)
from biospur_fusion.ingest.v47 import decode_measurements


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FAILURE_ROOT = (
    ROOT / "logs/c2_h02_native200_unified_full_nondegenerate_v3_20260905_105000"
)
DEFAULT_ANALYTIC_RESULT = (
    ROOT / "logs/c2_native200_orientation_constrained_biomechanics_v4_20260904/"
    "FINAL_RESULT.json"
)
DEFAULT_CALIBRATION_REPORT = (
    ROOT / "logs/c2_native200_calibration_v3_20260904/"
    "POSE_RESET_QMT_DIAGNOSTIC.json"
)
DEFAULT_PRE_IK_REPORT = (
    ROOT / "logs/c2_hxx_native200_calibration_v3_20260904/"
    "HXX_FROZEN_C2_REPLAY_REPORT.json"
)


def _json_ready(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    return value


def _bootstrap_time(
    pelvis_rows: list[dict[str, Any]],
    emitted_time_s: np.ndarray,
    action_stop_s: float,
) -> tuple[float, float]:
    pelvis_time = np.asarray([row["time_s"] for row in pelvis_rows])
    emitted = np.asarray(emitted_time_s, dtype=float)
    candidates = []
    for start in range(len(pelvis_time) - len(emitted) + 1):
        stop = start + len(emitted)
        if pelvis_time[stop - 1] >= action_stop_s:
            continue
        error = float(np.max(np.abs(
            np.diff(pelvis_time[start:stop]) - np.diff(emitted)
        )))
        if error <= 1e-8:
            candidates.append((start, error))
    if not candidates:
        raise RuntimeError("failure trajectory does not match pelvis IMU grid")
    # The runner emits every pelvis row after bootstrap through action stop.
    start, error = max(candidates, key=lambda row: row[0])
    return float(pelvis_time[start] - emitted[0]), error


def _replay(
    *,
    detector: AnkleContactDetector,
    ankle_rows: list[dict[str, Any]],
    body: FrozenHoldoutBodyProxy,
    alignment: np.ndarray,
    action_start_s: float,
    action_stop_s: float,
    bootstrap_time_s: float,
    emitted_time_s: np.ndarray,
    root_position_world_m: np.ndarray,
) -> dict[str, Any]:
    duration = action_stop_s - action_start_s
    owner_height_world_m: dict[str, float] = {}
    latest = {
        side: FootContactEvidence(
            side,
            bootstrap_time_s,
            0.0,
            False,
            np.nan,
            np.nan,
            np.nan,
            "NO_SAMPLE",
            support_state=FootSupportState.UNOBSERVABLE.value,
        )
        for side in ("left", "right")
    }
    transitions = []
    focus = []
    state_counts = {
        side: {state.value: 0 for state in FootSupportState}
        for side in ("left", "right")
    }
    emitted_absolute_time = bootstrap_time_s + np.asarray(
        emitted_time_s, dtype=float
    )
    root_sample_period_s = float(np.median(np.diff(emitted_absolute_time)))
    maximum_root_age_s = 1.5 * root_sample_period_s

    def ankle_offsets(query_time_s: float) -> dict[str, np.ndarray]:
        fraction = float(np.clip(
            (query_time_s - action_start_s) / duration, 0.0, 1.0
        ))
        offsets, _normals, _frame = body.at_fraction(
            "H02_golf", fraction, alignment
        )
        return {
            side: np.asarray(offsets[node], dtype=float)
            for node, side in ANKLE_NODE_TO_SIDE.items()
        }

    def causal_root_z(query_time_s: float) -> tuple[float, float, bool]:
        index = int(np.searchsorted(
            emitted_absolute_time, query_time_s, side="right"
        )) - 1
        if index < 1:
            return np.nan, np.inf, False
        age = float(query_time_s - emitted_absolute_time[index])
        if age < -1e-12 or age > maximum_root_age_s:
            return np.nan, age, False
        dt = float(
            emitted_absolute_time[index] - emitted_absolute_time[index - 1]
        )
        velocity_z = float(
            (root_position_world_m[index, 2]
             - root_position_world_m[index - 1, 2]) / dt
        )
        return (
            float(root_position_world_m[index, 2] + velocity_z * age),
            age,
            True,
        )

    for row in ankle_rows:
        query = float(row["time_s"])
        if not bootstrap_time_s < query < action_stop_s:
            continue
        side = str(row["side"])
        offsets = ankle_offsets(query)
        prior = ankle_offsets(max(action_start_s, query - 0.005))
        speed = float(np.linalg.norm((offsets[side] - prior[side]) / 0.005))
        lower_height = min(offset[2] for offset in offsets.values())
        relative_height = float(offsets[side][2] - lower_height)
        root_z, root_age_s, root_observable = causal_root_z(query)
        ankle_world_z = root_z + float(offsets[side][2])
        if side in owner_height_world_m:
            swing_observable = root_observable
            positive_swing = bool(
                swing_observable and
                ankle_world_z - owner_height_world_m[side]
                >= detector.config.maximum_height_margin_m
            )
            swing_height = (
                ankle_world_z - owner_height_world_m[side]
                if swing_observable else np.nan
            )
            swing_owner = "OWNED_FOOTHOLD_WORLD_Z"
        else:
            other = "right" if side == "left" else "left"
            swing_observable = latest[other].is_confirmed_stance
            positive_swing = bool(
                swing_observable
                and offsets[side][2] - offsets[other][2]
                >= detector.config.maximum_height_margin_m
            )
            swing_height = (
                float(offsets[side][2] - offsets[other][2])
                if swing_observable else np.nan
            )
            swing_owner = (
                "OPPOSITE_CONFIRMED_STANCE_RELATIVE_HEIGHT"
                if swing_observable else "UNOBSERVABLE"
            )
        previous_state = detector.support_state(side)
        evidence = detector.update(
            side,
            time_s=query,
            acceleration_mps2=row["acceleration"],
            gyro_rad_s=row["gyro"],
            relative_height_m=relative_height,
            relative_speed_mps=speed,
            positive_swing=positive_swing,
            swing_observable=swing_observable,
        )
        latest[side] = evidence
        current_state = evidence.resolved_support_state
        if (
            current_state is FootSupportState.STANCE_CONFIRMED
            and side not in owner_height_world_m
            and root_observable
        ):
            owner_height_world_m[side] = ankle_world_z
        elif (
            current_state is FootSupportState.SWING_CONFIRMED
            and side in owner_height_world_m
        ):
            del owner_height_world_m[side]
        state_counts[side][current_state.value] += 1
        if current_state is not previous_state:
            transitions.append({
                "time_s": query - bootstrap_time_s,
                "side": side,
                "from": previous_state.value,
                "to": current_state.value,
                "reason": evidence.reason,
                "confidence": evidence.confidence,
                "relative_height_m": relative_height,
                "relative_speed_mps": speed,
                "positive_swing": positive_swing,
                "swing_observable": swing_observable,
                "swing_height_m": swing_height,
                "swing_height_owner": swing_owner,
                "prior_held": evidence.prior_held,
                "root_owner_age_s": root_age_s,
                "root_owner_observable": root_observable,
                "foothold_identity_retained": side in owner_height_world_m,
            })
        relative_time = query - bootstrap_time_s
        if side == "right" and (
            abs(relative_time - 2.845886246446753) <= 0.08
            or abs(relative_time - 8.640801226050826) <= 0.08
        ):
            focus.append({
                "time_s": relative_time,
                "state": current_state.value,
                "reason": evidence.reason,
                "confidence": evidence.confidence,
                "relative_height_m": relative_height,
                "relative_speed_mps": speed,
                "positive_swing": positive_swing,
                "swing_observable": swing_observable,
                "swing_height_m": swing_height,
                "swing_height_owner": swing_owner,
                "prior_held": evidence.prior_held,
                "root_owner_age_s": root_age_s,
                "root_owner_observable": root_observable,
                "foothold_identity_retained": side in owner_height_world_m,
            })
    return {
        "stationary_no_flight_prior": detector.stationary_no_flight_prior,
        "transitions": transitions,
        "focus_right": focus,
        "state_counts": state_counts,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--failure-root", type=Path, default=DEFAULT_FAILURE_ROOT
    )
    args = parser.parse_args()
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    failure_root = args.failure_root.resolve()
    failure_npz = failure_root / "CONTACT_COHERENCE_FAILURE.npz"

    clocks = _clock_models(DEFAULT_CLOCK)
    bridges = _beacon_boundary_bridges(DEFAULT_CLOCK)
    episode = _load_holdout("H02_golf", clocks, bridges)
    events, _decode = decode_measurements(episode["raw"])
    ankle_rows = _ankle_imu_rows(
        events, clocks, episode["lo"], episode["hi"]
    )
    config = AnkleContactConfig()
    profiles = _fit_contact_profiles(clocks, bridges, config)
    trajectory, _model, owner = _load_analytic_pose_owner(
        DEFAULT_ANALYTIC_RESULT,
        DEFAULT_CALIBRATION_REPORT,
        DEFAULT_PRE_IK_REPORT,
    )
    calibration = load_frozen_c2_3a()
    body = FrozenHoldoutBodyProxy.create(
        load_frozen_c2_hxx_diagnostics(),
        calibration,
        trajectory=trajectory,
    )
    alignment, _forward = frozen_world_alignment(calibration)
    with np.load(failure_npz, allow_pickle=False) as archive:
        emitted_time = np.asarray(archive["time_s"], dtype=float)
        root_position = np.asarray(
            archive["fused_root_position_world_m"], dtype=float
        )
    pelvis_rows, _audit = _pelvis_imu(
        events, clocks[PELVIS_NODE], episode["lo"], 0.0
    )
    bootstrap, grid_error = _bootstrap_time(
        pelvis_rows, emitted_time, episode["hi"] * 1e-9
    )
    runs = {
        "without_prior": _replay(
            detector=AnkleContactDetector(profiles, config),
            ankle_rows=ankle_rows,
            body=body,
            alignment=alignment,
            action_start_s=episode["lo"] * 1e-9,
            action_stop_s=episode["hi"] * 1e-9,
            bootstrap_time_s=bootstrap,
            emitted_time_s=emitted_time,
            root_position_world_m=root_position,
        ),
        "stationary_no_flight_prior": _replay(
            detector=AnkleContactDetector(
                profiles, config, stationary_no_flight_prior=True
            ),
            ankle_rows=ankle_rows,
            body=body,
            alignment=alignment,
            action_start_s=episode["lo"] * 1e-9,
            action_stop_s=episode["hi"] * 1e-9,
            bootstrap_time_s=bootstrap,
            emitted_time_s=emitted_time,
            root_position_world_m=root_position,
        ),
    }
    result = {
        "schema": "biospur-c2-h02-tristate-contact-replay-v1",
        "status": "DETECTOR_ONLY_DIAGNOSTIC",
        "fusion_rerun": False,
        "detector_pose_owner": owner["trajectory_owner"],
        "uwb_articulated_correction_used_for_classification": False,
        "failure_npz": {
            "path": str(failure_npz),
            "sha256": _sha256(failure_npz),
        },
        "time_axis": {
            "bootstrap_time_s": bootstrap,
            "emitted_grid_match_maximum_error_s": grid_error,
            "root_owner": "CAUSAL_LAST_EMITTED_ROOT_Z_PLUS_PAST_FINITE_DIFFERENCE_VELOCITY",
            "maximum_root_owner_age_s": 1.5 * float(np.median(np.diff(emitted_time))),
        },
        "positive_swing_owner": {
            "with_owned_foothold": "ANALYTIC_BASE_ANKLE_WORLD_Z_MINUS_OWNED_FOOTHOLD_WORLD_Z",
            "without_owned_foothold": "BILATERAL_ANALYTIC_BASE_RELATIVE_HEIGHT_ONLY_IF_OTHER_STANCE_CONFIRMED",
            "both_unowned": "UNOBSERVABLE",
            "threshold_m": config.maximum_height_margin_m,
        },
        "runs": runs,
        "scientific_pass": False,
    }
    output.write_text(
        json.dumps(_json_ready(result), indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "output": str(output),
        "sha256": _sha256(output),
        "transitions": {
            key: len(value["transitions"]) for key, value in runs.items()
        },
    }, indent=2))


if __name__ == "__main__":
    main()
