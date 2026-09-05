#!/usr/bin/env python3
"""Held-node gate for the C2 articulated raw-range update.

At each sampled UWB epoch one complete body node is removed.  The remaining
nine nodes determine the shared root and articulated correction; the omitted
node is then evaluated without entering either solve.  This distinguishes
actual FK/IK propagation from in-sample range fitting.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import time

import numpy as np

from biospur_fusion.c2_3a_kinematics import (
    load_frozen_c2_3a,
    load_frozen_c2_hxx_diagnostics,
)
from biospur_fusion.c2_uwb_calibration.antenna_los import rotation_from_wxyz
from biospur_fusion.c2_uwb_calibration.articulated_range import (
    SEGMENTS,
    solve_articulated_ranges,
)
from biospur_fusion.c2_uwb_calibration.frozen_body_proxy import (
    FrozenHoldoutBodyProxy,
    NODE_TO_PROXY_POINT,
    body_proxy_at_fraction,
    frozen_world_alignment,
    nearest_frame,
)
from biospur_fusion.c2_uwb_calibration.pair_bias import load_pair_bias_table
from biospur_fusion.c2_uwb_calibration.shared_root import solve_shared_root
from biospur_fusion.c2_uwb_root_world.run_calibration import (
    _beacon_boundary_bridges,
    _clock_models,
)
from biospur_fusion.c2_uwb_root_world.calibration import CALIBRATION_ORDER

from evaluate_c2_hxx_shared_root_regression import (
    DEFAULT_BIAS_TABLE,
    DEFAULT_CLOCK,
    HOLDOUTS,
    _load_holdout,
)
from evaluate_c2_pair_bias_gate import (
    _build_links,
    _load_episode,
    _load_layout,
    _prediction,
    _reference_time,
    _tracker,
    _update_tracker,
)
from run_c2_h01_shared_root_imu_fusion import _usable_epoch_groups


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _residual(
    links,
    *,
    anchors: np.ndarray,
    root: np.ndarray,
    velocity: np.ndarray,
    node_position: np.ndarray | None = None,
) -> np.ndarray:
    rows = []
    for link in links:
        tag = (
            root + link.tag_offset_world_m
            if node_position is None else node_position
        ) + link.link_dt_s * velocity
        rows.append(link.range_m - np.linalg.norm(tag - anchors[link.anchor]))
    return np.asarray(rows)


def run(
    output: Path,
    *,
    action: str,
    clock_path: Path = DEFAULT_CLOCK,
    bias_table_path: Path = DEFAULT_BIAS_TABLE,
    stride: int = 4,
) -> dict:
    started = time.perf_counter()
    if stride < 1:
        raise ValueError("stride must be positive")
    output.mkdir(parents=True, exist_ok=False)
    clocks = _clock_models(clock_path)
    bridges = _beacon_boundary_bridges(clock_path)
    anchors, delays, tag_delay, layout_sigma = _load_layout()
    calibration = load_frozen_c2_3a()
    alignment, _ = frozen_world_alignment(calibration)
    if action in CALIBRATION_ORDER:
        pose_source = calibration
        episode = _load_episode(action, clocks, bridges)

        def proxy_at_fraction(fraction: float):
            return body_proxy_at_fraction(
                calibration, episode["episode_key"], fraction, alignment
            )

    elif action in HOLDOUTS:
        pose_source = load_frozen_c2_hxx_diagnostics()
        body = FrozenHoldoutBodyProxy.create(pose_source, calibration)
        episode = _load_holdout(action, clocks, bridges)

        def proxy_at_fraction(fraction: float):
            return body.at_fraction(action, fraction, alignment)

    else:
        raise ValueError(f"unsupported action: {action}")
    groups, group_audit = _usable_epoch_groups(episode, clocks)
    biases = (
        None if action == "00_initial_still" or action in HOLDOUTS
        else load_pair_bias_table(bias_table_path, nodes=NODE_TO_PROXY_POINT)
    )
    policy = "raw_all" if biases is None else "bounded_bias_variance"
    tracker = _tracker(np.array([
        float(np.mean(anchors[:, 0])),
        float(np.mean(anchors[:, 1])),
        0.95,
    ]))
    previous = {segment: np.zeros(3) for segment in SEGMENTS}
    rows = []
    failures = Counter()
    attempted = 0
    for sequence, group in enumerate(groups):
        if sequence % stride:
            continue
        attempted += 1
        epoch_ns = int(np.median([
            int(round(
                clocks[row.node].a_ns_per_us * row.strobe_us
                + clocks[row.node].b_ns
            ))
            for row in group
        ]))
        fraction = (epoch_ns - episode["lo"]) / (episode["hi"] - episode["lo"])
        offsets, normals, frame = proxy_at_fraction(fraction)
        rotations = {
            segment: alignment @ rotation_from_wxyz(
                pose_source.series(episode["episode_key"], segment)
                .quat_world_segment_wxyz[frame]
            )
            for segment in SEGMENTS
        }
        reference = _reference_time(group, clocks)
        predicted, dt = _prediction(tracker, reference)
        links = _build_links(
            group,
            offsets=offsets,
            normals=normals,
            anchors=anchors,
            delays=delays,
            tag_delay=tag_delay,
            layout_sigma=layout_sigma,
            clocks=clocks,
            reference_time_s=reference,
            predicted_root=predicted,
            velocity=tracker["velocity"],
            policy=policy,
            biases=biases,
        )
        nodes = sorted({link.node for link in links})
        held_node = nodes[(attempted - 1) % len(nodes)]
        training = [link for link in links if link.node != held_node]
        held = [link for link in links if link.node == held_node]
        baseline = solve_shared_root(
            training,
            anchors_m=anchors,
            initial_root_m=predicted,
            root_velocity_mps=tracker["velocity"],
        )
        if not baseline.success:
            failures[f"root:{baseline.reason}"] += 1
            continue
        articulated = solve_articulated_ranges(
            training,
            anchors_m=anchors,
            base_rotations_world=rotations,
            geometry=calibration.geometry,
            initial_root_m=baseline.root_position_m,
            root_velocity_mps=tracker["velocity"],
            previous_correction_rotvec=previous,
            use_facing_nlos_prior=True,
        )
        if not articulated.success:
            failures[
                f"ik:{articulated.reason}:nfev={articulated.nfev}:"
                f"rank={articulated.rank}:condition={articulated.condition:.6g}"
            ] += 1
            _update_tracker(tracker, baseline.root_position_m, reference, dt)
            continue
        before = _residual(
            held, anchors=anchors, root=baseline.root_position_m,
            velocity=tracker["velocity"]
        )
        after = _residual(
            held,
            anchors=anchors,
            root=baseline.root_position_m,
            velocity=tracker["velocity"],
            node_position=articulated.node_position_m[held_node]
            - articulated.root_position_m + baseline.root_position_m,
        )
        rows.append({
            "sequence": sequence,
            "held_node": held_node,
            "link_count": len(held),
            "before_median_abs_m": float(np.median(np.abs(before))),
            "after_median_abs_m": float(np.median(np.abs(after))),
            "before_rms_m": float(np.sqrt(np.mean(np.square(before)))),
            "after_rms_m": float(np.sqrt(np.mean(np.square(after)))),
            "maximum_segment_correction_rad": float(max(
                np.linalg.norm(value)
                for value in articulated.segment_correction_rotvec.values()
            )),
        })
        previous = {
            segment: value.copy()
            for segment, value in articulated.segment_correction_rotvec.items()
        }
        _update_tracker(tracker, baseline.root_position_m, reference, dt)

    if not rows:
        raise RuntimeError("no held-node articulated solve succeeded")
    before = np.asarray([row["before_median_abs_m"] for row in rows])
    after = np.asarray([row["after_median_abs_m"] for row in rows])
    before_rms = np.asarray([row["before_rms_m"] for row in rows])
    after_rms = np.asarray([row["after_rms_m"] for row in rows])
    gate = {
        "median_held_node_abs_error_noninferior": float(np.median(after))
        <= float(np.median(before)),
        "median_held_node_rms_noninferior": float(np.median(after_rms))
        <= float(np.median(before_rms)),
        "at_least_half_epochs_improve": float(np.mean(after < before)) >= 0.5,
        "at_least_85pct_attempted_solve": len(rows) / attempted >= 0.85,
    }
    result = {
        "schema": "biospur.c2.articulated_held_node_gate.v1",
        "status": "PASS" if all(gate.values()) else "REJECT",
        "scientific_pass": False,
        "action": action,
        "pose_source": "FROZEN_HXX" if action in HOLDOUTS else "FROZEN_C2_3A",
        "stride": stride,
        "attempted": attempted,
        "accepted": len(rows),
        "failures": dict(failures),
        "gate": gate,
        "metrics": {
            "before_median_abs_m": float(np.median(before)),
            "after_median_abs_m": float(np.median(after)),
            "change_median_abs_fraction": float(np.median(after) / np.median(before) - 1.0),
            "before_median_rms_m": float(np.median(before_rms)),
            "after_median_rms_m": float(np.median(after_rms)),
            "change_median_rms_fraction": float(
                np.median(after_rms) / np.median(before_rms) - 1.0
            ),
            "epoch_improvement_fraction": float(np.mean(after < before)),
        },
        "group_audit": group_audit,
        "clock": {
            "path": str(clock_path),
            "sha256": _sha256(clock_path),
            "global": "BEACON_LBD",
            "link_epoch": "STROBE_PLUS_T_ROUND_OVER_2",
        },
        "boundary": (
            "HELD_NODE_MECHANISM_GATE_ONLY;DISPLAY_PROXY_NOT_PHASE_CENTRES;"
            "NO_EXTERNAL_POSITION_TRUTH"
        ),
        "wall_s": time.perf_counter() - started,
    }
    (output / "ROWS.json").write_text(json.dumps(rows, indent=2) + "\n")
    (output / "RESULT.json").write_text(json.dumps(result, indent=2) + "\n")
    paths = sorted(path for path in output.iterdir() if path.name != "SHA256SUMS")
    (output / "SHA256SUMS").write_text("".join(
        f"{_sha256(path)}  {path.name}\n" for path in paths
    ))
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--action", required=True)
    parser.add_argument("--clock", type=Path, default=DEFAULT_CLOCK)
    parser.add_argument("--bias-table", type=Path, default=DEFAULT_BIAS_TABLE)
    parser.add_argument("--stride", type=int, default=4)
    args = parser.parse_args()
    result = run(
        args.output.resolve(),
        action=args.action,
        clock_path=args.clock.resolve(),
        bias_table_path=args.bias_table.resolve(),
        stride=args.stride,
    )
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
