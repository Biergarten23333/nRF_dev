"""Anonymous action-semantic global node association.

No hardware-to-role mapping appears in this module.  Scores are constructed
from anonymous per-action motion signatures and role-semantic templates, then a
global bijection is solved exactly.
"""

from __future__ import annotations

from collections import Counter
import math
from typing import Any

import numpy as np

from biospur_fusion.calibration_v2.association import topk_assignments, wilson_lower

ROLES = (
    "torso", "pelvis", "upper_arm_left", "upper_arm_right",
    "forearm_left", "forearm_right", "thigh_left", "thigh_right",
    "shank_left", "shank_right",
)


def _weights(**entries: float) -> np.ndarray:
    row = np.zeros(len(ROLES), dtype=float)
    for role, value in entries.items():
        row[ROLES.index(role)] = value
    return row


ACTION_ROLE_TEMPLATE = {
    "00_initial_still": _weights(),
    "02_t_pose": _weights(upper_arm_left=.75, upper_arm_right=.75, forearm_left=.55, forearm_right=.55),
    "03_pelvis_hula_circle": _weights(pelvis=1.0, torso=.45, thigh_left=.20, thigh_right=.20),
    "04_shoulder_left": _weights(upper_arm_left=1.0, forearm_left=.78, torso=.10),
    "05_shoulder_right": _weights(upper_arm_right=1.0, forearm_right=.78, torso=.10),
    "06_elbow_left": _weights(forearm_left=1.0, upper_arm_left=.22),
    "07_elbow_right": _weights(forearm_right=1.0, upper_arm_right=.22),
    "08_hip_left": _weights(thigh_left=1.0, shank_left=.40, pelvis=.28),
    "09_hip_right": _weights(thigh_right=1.0, shank_right=.40, pelvis=.28),
    "10_knee_left_seated": _weights(shank_left=1.0, thigh_left=.18),
    "11_knee_right_seated": _weights(shank_right=1.0, thigh_right=.18),
    "12_heel_raise_left": _weights(shank_left=1.0, thigh_left=.16),
    "13_heel_raise_right": _weights(shank_right=1.0, thigh_right=.16),
    "14_trunk_flex_extend": _weights(torso=1.0, pelvis=.48, thigh_left=.12, thigh_right=.12),
    "15_trunk_axial_rotation": _weights(torso=1.0, pelvis=.55, upper_arm_left=.12, upper_arm_right=.12),
    "16_squat": _weights(pelvis=.72, torso=.22, thigh_left=1.0, thigh_right=1.0, shank_left=.72, shank_right=.72),
    "17_final_still": _weights(),
    "18_heel_to_butt_left": _weights(shank_left=1.0, thigh_left=.28),
    "19_heel_to_butt_right": _weights(shank_right=1.0, thigh_right=.28),
}

ACTION_FAMILY = {
    "00_initial_still": "standing_context", "17_final_still": "standing_context",
    "02_t_pose": "bilateral_arm", "03_pelvis_hula_circle": "pelvis_trunk",
    "04_shoulder_left": "left_arm", "06_elbow_left": "left_arm",
    "05_shoulder_right": "right_arm", "07_elbow_right": "right_arm",
    "08_hip_left": "left_leg", "10_knee_left_seated": "left_leg",
    "12_heel_raise_left": "left_leg", "18_heel_to_butt_left": "left_leg",
    "09_hip_right": "right_leg", "11_knee_right_seated": "right_leg",
    "13_heel_raise_right": "right_leg", "19_heel_to_butt_right": "right_leg",
    "14_trunk_flex_extend": "pelvis_trunk", "15_trunk_axial_rotation": "pelvis_trunk",
    "16_squat": "bilateral_leg",
}


def action_activation(imu: dict[str, dict[str, np.ndarray]], nodes: tuple[str, ...]) -> np.ndarray:
    values = []
    for node in nodes:
        row = imu[node]
        gyro = row["gyro_raw"]
        acc = row["acc_raw"]
        gyro_dynamic = np.linalg.norm(gyro - np.median(gyro, axis=0), axis=1)
        acc_dynamic = np.linalg.norm(acc - np.median(acc, axis=0), axis=1)
        values.append(float(np.sqrt(np.mean(gyro_dynamic ** 2)) + .12 * np.sqrt(np.mean(acc_dynamic ** 2))))
    x = np.asarray(values)
    median = np.median(x)
    mad = np.median(np.abs(x - median)) + 1e-9
    return np.clip((x - median) / (1.4826 * mad), -3.0, 8.0)


def score_blocks(windows: dict[str, dict[str, Any]], nodes: tuple[str, ...]) -> tuple[list[str], np.ndarray, dict[str, list[float]]]:
    names, blocks, activation = [], [], {}
    for action_id, window in windows.items():
        if action_id not in ACTION_ROLE_TEMPLATE:
            continue
        x = action_activation(window["imu"], nodes)
        template = ACTION_ROLE_TEMPLATE[action_id]
        # Centring removes action-global body motion while retaining natural
        # coupled motion as soft evidence rather than requiring rigid stillness.
        score = np.outer(x, template - np.mean(template))
        names.append(action_id)
        blocks.append(score)
        activation[action_id] = x.tolist()
    return names, np.stack(blocks), activation


def mapping_key(mapping: dict[str, str], nodes: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(mapping[node] for node in nodes)


def stratified_bootstrap(nodes, block_names, block_scores, replicates, seed):
    rng = np.random.default_rng(seed)
    families: dict[str, list[int]] = {}
    for index, name in enumerate(block_names):
        families.setdefault(ACTION_FAMILY[name], []).append(index)
    keys, margins = [], []
    for _ in range(replicates):
        chosen = []
        for indices in families.values():
            chosen.extend(rng.choice(indices, size=len(indices), replace=True).tolist())
        score = block_scores[chosen].mean(axis=0)
        top = topk_assignments(nodes, ROLES, score, 2)
        keys.append(mapping_key(top[0]["mapping"], nodes))
        margins.append(top[0]["score"] - top[1]["score"])
    counts = Counter(keys)
    winner, wins = counts.most_common(1)[0]
    binding = {}
    for i, node in enumerate(nodes):
        count = sum(key[i] == winner[i] for key in keys)
        binding[node] = {
            "role": winner[i],
            "frequency": count / replicates,
            "wilson_lower_one_sided_95": wilson_lower(count, replicates),
        }
    return {
        "replicates": replicates,
        "winner": dict(zip(nodes, winner)),
        "exact_top_rank_frequency": wins / replicates,
        "exact_top_rank_wilson_lower_one_sided_95": wilson_lower(wins, replicates),
        "per_binding": binding,
        "margin_mean": float(np.mean(margins)),
        "margin_standard_error": float(np.std(margins, ddof=1) / math.sqrt(replicates)),
        "selection_counts_top20": [{"mapping_key": list(k), "count": v} for k, v in counts.most_common(20)],
    }


def complete_block_permutation_null(nodes, block_scores, permutations, seed):
    rng = np.random.default_rng(seed)
    margins = []
    n_roles = block_scores.shape[2]
    for _ in range(permutations):
        shuffled = np.stack([block[:, rng.permutation(n_roles)] for block in block_scores])
        top = topk_assignments(nodes, ROLES, shuffled.mean(axis=0), 2)
        margins.append(top[0]["score"] - top[1]["score"])
    return {
        "valid_permutations": permutations,
        "null_statistic": "maximum global H1-H2 assignment margin per permutation",
        "margin_p99": float(np.quantile(margins, .99)),
        "margin_max": float(np.max(margins)),
        "resolution": 1.0 / (permutations + 1),
    }


def leave_one(nodes, block_names, block_scores, winner):
    key = mapping_key(winner, nodes)
    actions = {}
    for i, name in enumerate(block_names):
        score = np.delete(block_scores, i, axis=0).mean(axis=0)
        mapping = topk_assignments(nodes, ROLES, score, 1)[0]["mapping"]
        actions[name] = {"same_mapping": mapping_key(mapping, nodes) == key, "mapping": mapping}
    families = {}
    for family in sorted(set(ACTION_FAMILY.values())):
        keep = [i for i, name in enumerate(block_names) if ACTION_FAMILY[name] != family]
        mapping = topk_assignments(nodes, ROLES, block_scores[keep].mean(axis=0), 1)[0]["mapping"]
        families[family] = {"same_mapping": mapping_key(mapping, nodes) == key, "mapping": mapping}
    return actions, families
