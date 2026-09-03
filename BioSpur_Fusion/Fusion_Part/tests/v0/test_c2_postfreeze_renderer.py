from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from biospur_fusion.v0.c2_progressive.scientific_renderer import (
    POSTFREEZE_RETROSPECTIVE_DISPLAY_LABEL,
    POSTFREEZE_RETROSPECTIVE_QMT_SOURCE,
    _materialize_postfreeze_bilateral_flexion_sample,
    _validated_postfreeze_retrospective_authority,
    _validated_source_display,
)


WORKSPACE = Path("/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/Fusion_Part")
RUN = WORKSPACE / "logs/c2_basis_progressive_20260829T102836Z"
REPLAY = (
    RUN
    / "CONTINUATION_SPRINT"
    / "RUN013_POSTFREEZE_RETROSPECTIVE_QMT_REPLAY_002"
    / "POSTFREEZE_RETROSPECTIVE_QMT_STATE.json"
)


def _manifest() -> dict:
    return json.loads(REPLAY.read_text(encoding="utf-8"))


def _renderer_settings() -> dict:
    return _settings()["scientific_renderer"]


def _settings() -> dict:
    seal = json.loads(
        (RUN / "P2_PREFIT_REGISTRY_SEAL_021_SOURCE_CORRECTION_001.json").read_text(
            encoding="utf-8"
        )
    )
    amendment = json.loads(
        (WORKSPACE / seal["amendment"]["path"]).read_text(encoding="utf-8")
    )
    return amendment["effective_settings"]


def test_postfreeze_renderer_authenticates_the_exact_replay_owner() -> None:
    authority = _validated_postfreeze_retrospective_authority(
        workspace=WORKSPACE,
        manifest=_manifest(),
    )
    assert authority["source"] == POSTFREEZE_RETROSPECTIVE_QMT_SOURCE
    assert authority["persistent_heading_owner_instance_count"] == 1
    assert authority["official_callable"] == "qmt.headingCorrection"
    assert authority["time_varying_heading"]
    assert not authority["causal_progressive_state_modified"]
    assert not authority["scientific_acceptance_pass"]


def test_postfreeze_renderer_rejects_unverified_replay_source_label() -> None:
    with pytest.raises(RuntimeError, match="requires its exact owner authority"):
        _validated_source_display(
            physical_source=POSTFREEZE_RETROSPECTIVE_QMT_SOURCE,
            manifest=_manifest(),
            renderer=_renderer_settings(),
        )


def test_postfreeze_renderer_uses_the_exact_noncausal_label() -> None:
    label, scientific_source_verified = _validated_source_display(
        physical_source=POSTFREEZE_RETROSPECTIVE_QMT_SOURCE,
        manifest=_manifest(),
        renderer=_renderer_settings(),
        postfreeze_retrospective_source_verified=True,
    )
    assert label == POSTFREEZE_RETROSPECTIVE_DISPLAY_LABEL
    assert scientific_source_verified is False


def test_postfreeze_renderer_has_owner_support_for_exactly_four_early_checkpoints() -> None:
    manifest = _manifest()
    action_by_index = {
        int(row["chronological_index"]): str(row["action"])
        for row in manifest["structure"]["progressive_prefixes"]
    }
    expected = {
        0: "00_initial_still",
        3: "04_shoulder_left",
        7: "08_hip_left",
        8: "09_hip_right",
    }
    assert {index: action_by_index[index] for index in expected} == expected
    support = manifest["structure"]["physical_trajectory_support"]
    for index in expected:
        rows = support[str(index)]
        assert len(rows) == 4
        assert all(row["source"] == POSTFREEZE_RETROSPECTIVE_QMT_SOURCE for row in rows.values())
        assert all(row["postfreeze_retrospective"] for row in rows.values())
        assert all(row["time_varying_parent_plus_child_deltafilt"] for row in rows.values())
        assert all(not row["scientific_physical_gate_executed"] for row in rows.values())


def test_squat_viewer_selects_exact_rooted_bilateral_flexion_without_interpolation() -> None:
    manifest = _manifest()
    npz_path = WORKSPACE / manifest["npz"]["path"]
    branch_ids = tuple(
        manifest["structure"]["progressive_prefixes"][-1]["branch_ids"]
    )
    support_ids = tuple(
        manifest["structure"]["physical_trajectory_support"]["15"]
    )
    with np.load(npz_path, allow_pickle=False) as archive:
        weights = np.asarray(archive["frozen/branch_weights"], dtype=float)
        selection_branch = sorted(
            support_ids,
            key=lambda branch_id: (
                -float(weights[branch_ids.index(branch_id)]), branch_id,
            ),
        )[0]
        arrays = {
            name: np.asarray(archive[name]).copy()
            for name in archive.files
            if (
                name.startswith("orientation/15/")
                or name.startswith(f"heading/15/{selection_branch}/")
                or name.startswith("trajectory/15/")
                or name.startswith("physical_trajectory/15/")
                or (
                    name.startswith("frames/")
                    and "/segment_from_sensor/" in name
                )
            )
        }
    audit = _materialize_postfreeze_bilateral_flexion_sample(
        manifest=manifest,
        arrays=arrays,
        settings=_settings(),
        chronological_index=15,
        selection_branch_id=selection_branch,
    )
    assert audit["selected_common_physical_time_s"] == pytest.approx(
        5262.832653, abs=1e-9,
    )
    assert audit["selected_left_knee_flexion_deg"] == pytest.approx(
        137.3825123, abs=1e-5,
    )
    assert audit["selected_right_knee_flexion_deg"] == pytest.approx(
        81.4954304, abs=1e-5,
    )
    assert audit["left_knee_maximum_flexion_deg"] == pytest.approx(
        139.9239723, abs=1e-5,
    )
    assert audit["right_knee_maximum_flexion_deg"] == pytest.approx(
        81.4954304, abs=1e-5,
    )
    assert audit["common_exact_rooted_pelvis_row_count"] == 6950
    assert audit["nearest_row_or_interpolation_used"] is False
    assert audit["retained_branch_ids"] == sorted(support_ids)
    assert audit["payload_qmt_fit_or_progressive_rerun"] is False
    for branch_id in support_ids:
        assert np.allclose(
            arrays[f"physical_trajectory/15/{branch_id}/common_physical_time_s"],
            np.asarray([5262.832653]),
            rtol=0.0,
            atol=1e-12,
        )
        for segment in (
            "pelvis", "torso", "upper_arm_left", "forearm_left",
            "upper_arm_right", "forearm_right", "thigh_left", "shank_left",
            "thigh_right", "shank_right",
        ):
            covariance = arrays[
                f"physical_trajectory/15/{branch_id}/orientation_covariance/{segment}"
            ]
            assert covariance.shape == (1, 3, 3)
            assert np.min(np.linalg.eigvalsh(covariance[0])) >= -1e-8
