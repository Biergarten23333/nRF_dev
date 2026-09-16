from __future__ import annotations

import numpy as np
import pytest

from biospur_fusion.c2_uwb_root_world.action00_session_offsets import (
    corrected_epoch_consensus,
    fit_static_session_offsets,
    robust_corrected_epoch_consensus,
)


def test_prefix_fit_removes_stable_node_disagreement_on_heldout() -> None:
    root = np.array([1.8, 0.9, 1.0])
    offsets = {
        "a": np.array([0.4, -0.2, 0.1]),
        "b": np.array([-0.3, 0.1, -0.2]),
        "c": np.array([0.2, 0.3, 0.2]),
        "d": np.array([-0.1, -0.2, -0.1]),
    }
    samples = []
    for frame in range(40):
        for node, offset in offsets.items():
            samples.append((frame * 0.12, node, root + offset))
    profile = fit_static_session_offsets(
        samples, training_start_s=0.0, training_stop_s=3.0,
        minimum_samples=20, provenance="synthetic test",
    )
    centre, corrected = corrected_epoch_consensus(
        profile, {node: root + offset for node, offset in offsets.items()},
    )
    np.testing.assert_allclose(centre, profile.common_root_gauge_m, atol=1e-12)
    assert max(np.linalg.norm(value - centre) for value in corrected.values()) < 1e-12
    assert profile.digest


def test_fit_is_prefix_only_and_requires_four_supported_nodes() -> None:
    samples = [
        (float(frame), node, np.array([float(index), 0.0, 0.0]))
        for frame in range(4)
        for index, node in enumerate(("a", "b", "c"))
    ]
    with pytest.raises(ValueError, match="fewer than four"):
        fit_static_session_offsets(
            samples, training_start_s=0.0, training_stop_s=3.0,
            minimum_samples=2, provenance="synthetic test",
        )


def test_unowned_node_cannot_enter_corrected_consensus() -> None:
    samples = [
        (frame * 0.1, node, np.array([float(index), 0.0, 0.0]))
        for frame in range(30)
        for index, node in enumerate(("a", "b", "c", "d"))
    ]
    profile = fit_static_session_offsets(
        samples, training_start_s=0.0, training_stop_s=2.0,
        minimum_samples=10, provenance="synthetic test",
    )
    with pytest.raises(ValueError, match="insufficient"):
        corrected_epoch_consensus(profile, {"unknown": [0.0, 0.0, 0.0]})


def test_robust_consensus_rejects_one_node_without_forcing_replacement() -> None:
    samples = [
        (frame * 0.1, node, np.array([float(index), 0.0, 0.0]))
        for frame in range(30)
        for index, node in enumerate(("a", "b", "c", "d", "e"))
    ]
    profile = fit_static_session_offsets(
        samples, training_start_s=0.0, training_stop_s=2.0,
        minimum_samples=10, provenance="synthetic test",
    )
    candidates = {
        node: np.array([float(index), 0.0, 0.0])
        for index, node in enumerate(("a", "b", "c", "d", "e"))
    }
    candidates["e"] = candidates["e"] + np.array([2.0, 0.0, 0.0])
    result = robust_corrected_epoch_consensus(profile, candidates)
    assert result.trusted_nodes == ("a", "b", "c", "d")
    assert result.rejected_nodes == ("e",)
    np.testing.assert_allclose(result.root_position_m, profile.common_root_gauge_m)
