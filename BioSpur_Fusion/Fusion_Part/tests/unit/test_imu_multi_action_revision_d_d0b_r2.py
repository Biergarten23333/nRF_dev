import json
from pathlib import Path

import numpy as np

from biospur_fusion.imu_multi_action_revision_d.d0b_r1_generator import SEGMENTS
from biospur_fusion.imu_multi_action_revision_d.d0b_r1_model import R1Observation, blind_initialization
from biospur_fusion.imu_multi_action_revision_d.d0b_r2_lineage import (
    R2Objective, factor_definitions, factor_value_map, freeze_selection,
)


def contract():
    path = Path(__file__).parents[2] / "config/imu_multi_action_revision_d_d0b_r2/R2_SYNTHETIC_MODEL_CONTRACT.json"
    return json.loads(path.read_text())


def observation():
    count = 120
    time = np.arange(count, dtype=np.int64) * 20_000_000
    nodes = tuple(f"N{index}" for index in range(10))
    node_to_segment = dict(zip(nodes, SEGMENTS))
    rotation = np.tile(np.eye(3), (count, 10, 1, 1))
    gyro = np.zeros((count, 10, 3)); gyro[:, :, 0] = np.linspace(0.0, 1.0, count)[:, None]
    valid = np.ones((count, 10), bool)
    names = ("initial_still_attempt2", "t_pose", "arms", "left_elbow", "right_elbow_attempt2", "left_knee", "right_knee", "left_heel", "right_heel", "squats", "trunk")
    windows = {}; actions = {}
    for index, name in enumerate(names):
        rows = np.arange(index * 10, index * 10 + 10)
        windows[name] = (int(time[rows[0]]), int(time[rows[-1]]))
        actions[name] = {"BROAD_ACTIVE_ROWS": rows, "STATIC_PLATEAU_CANDIDATE": {"row_indices": rows.tolist()}}
    return R1Observation(time, nodes, rotation, gyro, valid, windows, node_to_segment, actions)


def test_r2_factor_and_row_identity_are_frozen_and_unique():
    obs = observation(); selections, manifest = freeze_selection(obs, contract())
    assert len(factor_definitions()) == len(selections) == 39
    assert len({item.factor_block_id for item in selections}) == 39
    ids = [row["residual_row_id"] for row in manifest["residual_rows"]]
    assert len(ids) == len(set(ids))
    assert all(row["selector_version"] == "D0B_R2_OBSERVATION_LINEAGE_V2" for row in manifest["residual_rows"])


def test_complete_pool_deletion_removes_factor_without_cross_namespace_backfill():
    obs = observation(); baseline, _ = freeze_selection(obs, contract())
    target = baseline[20]
    changed, _ = freeze_selection(obs, contract(), remove_complete_pool={target.factor_block_id})
    item = next(value for value in changed if value.factor_block_id == target.factor_block_id)
    assert item.status == "NO_SOURCE_SUPPORT"
    assert len(item.selected_rows) == 0
    assert all(value.action_id == base.action_id and value.phase_id == base.phase_id and value.chain_id == base.chain_id
               for value, base in zip(changed, baseline) if value.factor_block_id != target.factor_block_id)


def test_selected_row_withholding_backfills_only_from_same_candidate_pool():
    obs = observation(); baseline, _ = freeze_selection(obs, contract())
    target = baseline[20]
    changed, _ = freeze_selection(obs, contract(), withhold_selected={target.factor_block_id: target.selected_rows[:2]})
    item = next(value for value in changed if value.factor_block_id == target.factor_block_id)
    assert not np.intersect1d(item.selected_rows, target.selected_rows[:2]).size
    assert set(item.selected_rows).issubset(set(target.candidate_rows) - set(target.selected_rows[:2]))
    assert len(item.selected_rows) == len(set(item.selected_rows.tolist()))


def test_frozen_objective_does_not_reselect_and_zero_is_not_a_product():
    obs = observation(); selections, manifest = freeze_selection(obs, contract())
    x = blind_initialization(obs, contract())
    objective = R2Objective(obs, contract(), selections, manifest["manifest_sha256"])
    before = factor_value_map(objective, x)
    obs.valid[:] = False
    after = factor_value_map(objective, x)
    assert before.keys() == after.keys()
    assert all(np.array_equal(before[key], after[key]) for key in before)
    assert not any("zero" in item.factor for item in selections)
