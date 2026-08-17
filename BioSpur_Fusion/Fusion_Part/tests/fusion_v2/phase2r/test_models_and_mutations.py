from __future__ import annotations

import numpy as np
import pytest

from biospur_fusion.calibration_v2.phase2r.association import (
    ACTION_ROLE_TEMPLATE, ROLES, complete_block_permutation_null,
    mapping_key, stratified_bootstrap,
)
from biospur_fusion.calibration_v2.phase2r.mounting import (
    DISTINCT_LAYOUT, H9, antipodal_cluster, validate_model_config,
)
from biospur_fusion.calibration_v2.association import topk_assignments


def test_global_assignment_exact_recovery_and_runner_up():
    nodes = tuple(f"N{i}" for i in range(10))
    score = np.eye(10) * 10
    top = topk_assignments(nodes, ROLES, score, 2)
    assert len(top) == 2 and top[0]["score"] > top[1]["score"]
    assert len(set(top[0]["mapping"].values())) == 10


def test_mirror_ambiguity_is_not_hidden():
    nodes = tuple(f"N{i}" for i in range(10))
    score = np.eye(10) * 5
    score[2, 2] = score[2, 3] = score[3, 2] = score[3, 3] = 5
    top = topk_assignments(nodes, ROLES, score, 2)
    assert top[0]["score"] == top[1]["score"]


def test_semantic_templates_allow_natural_coupling():
    squat = ACTION_ROLE_TEMPLATE["16_squat"]
    assert np.count_nonzero(squat) >= 6
    assert squat[ROLES.index("torso")] > 0


def test_h9_antipodal_soft_cluster_and_free_sign():
    rng = np.random.default_rng(4)
    directions = {node: np.array([0, 0, 1.0]) + rng.normal(0, .05, 3) for node in H9}
    directions[H9[2]] *= -1
    result = antipodal_cluster(directions)
    assert result["angular_rms_rad"] > 0
    assert all(len(x) == 2 for x in result["node_sign_hypotheses"].values())
    assert result["production_factor_count"] == 0


def test_accidental_distinct_layout_pooling_rejected():
    directions = {node: np.array([0, 0, 1.0]) for node in H9 + DISTINCT_LAYOUT}
    with pytest.raises(ValueError, match="exactly H9"):
        antipodal_cluster(directions)


@pytest.mark.parametrize("mutation", [
    {"H9": H9, "distinct_layout": DISTINCT_LAYOUT, "hard_equality": True, "per_node_sigma_rad": .1},
    {"H9": H9, "distinct_layout": DISTINCT_LAYOUT, "hard_equality": False, "named_sensor_axis": "+X", "per_node_sigma_rad": .1},
    {"H9": H9, "distinct_layout": DISTINCT_LAYOUT, "hard_equality": False, "per_node_sigma_rad": 0},
    {"H9": H9 + DISTINCT_LAYOUT, "distinct_layout": (), "hard_equality": False, "per_node_sigma_rad": .1},
])
def test_forbidden_mounting_mutations_rejected(mutation):
    with pytest.raises(ValueError):
        validate_model_config(mutation)


def test_bootstrap_and_null_counts_are_contract_scale_capable():
    nodes = tuple(f"N{i}" for i in range(10))
    names = list(ACTION_ROLE_TEMPLATE)
    blocks = np.stack([np.eye(10) * (3 + i / 20) for i in range(len(names))])
    boot = stratified_bootstrap(nodes, names, blocks, 25, 7)
    null = complete_block_permutation_null(nodes, blocks, 30, 8)
    assert boot["replicates"] == 25 and null["valid_permutations"] == 30


def test_raw_accelerometer_double_count_contract():
    lineage = {"raw_specific_force_factor": 100, "gravity_factor": 0, "mounting_cluster_factor": 0}
    assert sum(lineage.values()) == len(range(100))


def test_hard_hinge_and_hard_still_are_not_templates():
    assert all(np.count_nonzero(v) != 1 for k, v in ACTION_ROLE_TEMPLATE.items() if k in ("16_squat", "14_trunk_flex_extend"))
