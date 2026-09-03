#!/usr/bin/env python3
"""Render a fixed-camera arm-branch comparison from fresh attempt 003.

This is a derived-only diagnostic.  It never reads payload, reruns an owner,
changes branch weights, or authorizes held-out/scientific acceptance.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

import matplotlib.pyplot as plt
import numpy as np


WORKSPACE = Path("/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/Fusion_Part")
RUN = WORKSPACE / "logs/c2_basis_progressive_20260829T102836Z"
SPRINT = RUN / "CONTINUATION_SPRINT"
MANIFEST = SPRINT / "C2_FRESH_CONTINUATION_FROZEN_STATE_003.json"
NPZ = SPRINT / "C2_FRESH_CONTINUATION_FROZEN_STATE_003.npz"
AMENDMENT = RUN / "P2_PREFIT_ACTIVE_PARAMETER_REGISTRY_AMENDMENT_021_SOURCE_CORRECTION_001.json"
OUT = SPRINT / "C2_FRESH_ARM_BRANCH_DIAGNOSTIC_003_R2"


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _array_sha(value: np.ndarray) -> str:
    array = np.ascontiguousarray(np.asarray(value))
    header = json.dumps(
        {"dtype": str(array.dtype), "shape": list(array.shape)}, sort_keys=True,
    ).encode()
    return hashlib.sha256(header + array.tobytes()).hexdigest()


def _write_new(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
    path.chmod(0o444)


def main() -> int:
    if Path.cwd().resolve() != WORKSPACE:
        raise RuntimeError("fresh arm diagnostic requires canonical Fusion_Part")
    from biospur_fusion.v0.c2_progressive.scientific_fk import (
        direct_orientation_avatar_fk,
        landmark_proxy_sensitivity_profiles,
    )

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    settings = json.loads(AMENDMENT.read_text(encoding="utf-8"))["effective_settings"]
    verification = manifest["fresh_verification"]
    if (
        verification.get("pass") is not True
        or verification.get("fresh_execution_role") != "FRESH_RAW_RECOMPUTATION"
        or verification.get("primary_reader_session_id")
        == verification.get("fresh_reader_session_id")
        or manifest.get("heldout_opened") is not False
        or manifest.get("scientific_acceptance_pass") is not False
    ):
        raise RuntimeError("attempt003 fresh-only diagnostic authority is inconsistent")
    support = manifest["structure"]["physical_trajectory_support"]["16"]
    branch_ids = tuple(sorted(support))
    if len(branch_ids) != 4 or not all(support[value]["physically_legal"] for value in branch_ids):
        raise RuntimeError("fresh final-still hard-supported branch closure changed")
    profile = landmark_proxy_sensitivity_profiles(settings["anthropometric_proxy"])[0]
    renderer = settings["scientific_renderer"]
    frame_branches = manifest["structure"]["frame_branches"]
    canonical_ids = tuple(row["branch_id"] for row in frame_branches)
    segments = (
        "pelvis", "torso", "upper_arm_left", "forearm_left",
        "upper_arm_right", "forearm_right", "thigh_left", "shank_left",
        "thigh_right", "shank_right",
    )
    line_groups = {
        "upper_arm_left": "left", "forearm_left": "left",
        "upper_arm_right": "right", "forearm_right": "right",
    }
    colors = {"left": "#2563eb", "right": "#dc2626", "other": "#374151"}
    rows: list[dict[str, Any]] = []
    results: dict[str, Any] = {}
    common_time_reference: np.ndarray | None = None
    with np.load(NPZ, allow_pickle=False) as arrays:
        weights = np.asarray(arrays["frozen/branch_weights"], dtype=float)
        hard_support = np.asarray(arrays["frozen/branch_hard_support"], dtype=bool)
        for branch_id in branch_ids:
            canonical_index = canonical_ids.index(branch_id)
            if not hard_support[canonical_index]:
                raise RuntimeError("diagnostic branch is absent from authoritative hard support")
            prefix = f"physical_trajectory/16/{branch_id}"
            common_time = np.asarray(arrays[f"{prefix}/common_physical_time_s"], dtype=float)
            if common_time_reference is None:
                common_time_reference = common_time.copy()
            elif not np.array_equal(common_time, common_time_reference):
                raise RuntimeError("fresh final-still branch grids are not exact-equal")
            sample_index = int(common_time.size // 2)
            rotations = {
                segment: np.asarray(
                    arrays[f"{prefix}/world_from_segment/{segment}"][sample_index],
                    dtype=float,
                )
                for segment in segments
            }
            result = direct_orientation_avatar_fk(
                world_from_segment=rotations,
                profile=profile,
                pelvis_gauge_position_m=np.zeros(3),
            )
            results[branch_id] = result
            points = result.landmark_positions_m
            vectors = {
                "upper_arm_left": points["elbow_left"] - points["shoulder_left_attach_proxy"],
                "forearm_left": points["wrist_left"] - points["elbow_left"],
                "upper_arm_right": points["elbow_right"] - points["shoulder_right_attach_proxy"],
                "forearm_right": points["wrist_right"] - points["elbow_right"],
            }
            rows.append({
                "branch_id": branch_id,
                "canonical_branch_index": canonical_index,
                "posterior_weight": float(weights[canonical_index]),
                "hard_supported": True,
                "full_physical_gate_legal": True,
                "sample_index": sample_index,
                "common_physical_time_s": float(common_time[sample_index]),
                "common_time_array_sha256": _array_sha(common_time),
                "arm_vectors_m": {
                    name: value.tolist() for name, value in vectors.items()
                },
                "all_arm_longitudinal_vectors_world_z_down": bool(
                    all(float(value[2]) < 0.0 for value in vectors.values())
                ),
            })
    assert common_time_reference is not None
    selected_times = {row["common_physical_time_s"] for row in rows}
    if len(selected_times) != 1:
        raise RuntimeError("fresh arm comparison does not use one exact timestamp")

    figure, axes = plt.subplots(
        1, 4, figsize=(18.0, 5.6), dpi=int(renderer["dpi"]), sharex=True, sharey=True,
    )
    for axis, row in zip(axes, rows, strict=True):
        result = results[row["branch_id"]]
        for name, line in sorted(result.line_segments_m.items()):
            group = line_groups.get(name, "other")
            axis.plot(
                line[:, 1], line[:, 2], color=colors[group],
                linewidth=float(renderer["line_width"]) * (1.3 if group != "other" else 0.9),
                alpha=1.0 if group != "other" else 0.72,
            )
        points = np.vstack(list(result.landmark_positions_m.values()))
        axis.scatter(points[:, 1], points[:, 2], s=13, color="#111827", zorder=4)
        arm_signs = row["branch_id"].replace("HINGE_SIGN_", "").split("_knee_left")[0]
        axis.set_title(
            f"{arm_signs.replace('_elbow_right:', ' / R:').replace('elbow_left:', 'L:')}\n"
            f"weight={row['posterior_weight']:.6f}; hard/legal",
            fontsize=9,
        )
        axis.set_xlabel("fresh replay-world/gauge y (m)")
        axis.set_xlim(*map(float, renderer["horizontal_limits_m"]))
        axis.set_ylim(*map(float, renderer["vertical_limits_m"]))
        axis.set_aspect("equal", adjustable="box")
        axis.grid(alpha=0.2)
    axes[0].set_ylabel("fresh replay-world/gauge z (m)")
    figure.suptitle(
        "ATTEMPT003 FRESH TRAINING-ONLY — FINAL-STILL ARM BRANCH COMPARISON\n"
        "same exact owner timestamp/camera/geometry; no branch selected by pixels; NOT POSE/SCIENCE PASS",
        color="#991b1b", fontsize=12, fontweight="bold",
    )
    figure.text(
        0.5, 0.018,
        f"t={next(iter(selected_times)):.6f} s | {profile['profile_id']} isolates branch effect | "
        "all four alternatives retained; no IK/rebase/repair/manual flip",
        ha="center", fontsize=8.5,
    )
    figure.tight_layout(rect=(0.01, 0.14, 0.99, 0.88))
    OUT.mkdir(parents=True, exist_ok=False)
    png = OUT / "FRESH_FINAL_STILL_FOUR_ARM_BRANCH_FRONT_COMPARISON.png"
    figure.savefig(png)
    plt.close(figure)
    png.chmod(0o444)

    vector_stack = {
        name: np.asarray([row["arm_vectors_m"][name] for row in rows], dtype=float)
        for name in line_groups
    }
    maximum_endpoint_spread = {
        name: float(max(
            np.linalg.norm(values[first] - values[second])
            for first in range(len(values)) for second in range(first + 1, len(values))
        ))
        for name, values in vector_stack.items()
    }
    audit = {
        "schema": "biospur-c2-fresh-arm-branch-front-comparison-v1",
        "fresh_manifest": {"path": str(MANIFEST.relative_to(WORKSPACE)), "sha256": _sha(MANIFEST)},
        "fresh_npz": {"path": str(NPZ.relative_to(WORKSPACE)), "sha256": _sha(NPZ)},
        "source": {
            "path": "tools/render_c2_fresh_arm_branch_diagnostic.py",
            "sha256": _sha(Path(__file__).resolve()),
        },
        "artifact": {"path": str(png.relative_to(WORKSPACE)), "sha256": _sha(png)},
        "action": "17_final_still",
        "chronological_index": 16,
        "branch_rows": rows,
        "maximum_interbranch_arm_vector_spread_m": maximum_endpoint_spread,
        "upper_arm_longitudinal_vectors_identical_across_hinge_sign_branches": bool(
            maximum_endpoint_spread["upper_arm_left"] <= 1e-12
            and maximum_endpoint_spread["upper_arm_right"] <= 1e-12
        ),
        "arm_branch_selection_resolves_longitudinal_or_loop_defect": False,
        "causal_decision": "RETAIN_ALL_FOUR_HARD_SUPPORTED_BRANCHES_AND_PRESERVE_MULTIMODAL_UNCERTAINTY",
        "dominant_weight_used_as_hard_candidate_lock": False,
        "pixel_evidence_used_for_branch_selection": False,
        "payload_read": False,
        "heldout_opened": False,
        "fit_qmt_or_progressive_state_rerun_or_modified": False,
        "manual_flip_ik_rebase_repair": False,
        "scientific_acceptance_pass": False,
    }
    _write_new(OUT / "FRESH_ARM_BRANCH_COMPARISON_AUDIT.json", audit)
    print(json.dumps({
        "artifact": audit["artifact"],
        "audit": {
            "path": str((OUT / "FRESH_ARM_BRANCH_COMPARISON_AUDIT.json").relative_to(WORKSPACE)),
            "sha256": _sha(OUT / "FRESH_ARM_BRANCH_COMPARISON_AUDIT.json"),
        },
        "causal_decision": audit["causal_decision"],
        "scientific_acceptance_pass": False,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
