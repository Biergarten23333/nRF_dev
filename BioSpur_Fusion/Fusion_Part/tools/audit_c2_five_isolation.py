#!/usr/bin/env python3
"""Verify actual file access denial inside the five-node mount namespace."""
import argparse
import json
from pathlib import Path

from biospur_fusion.c2_imucoco.workflow import INPUT_RUN, load_input, write
from biospur_fusion.c2_five_calibration.frontend import FIT
from biospur_fusion.c2_sparse_nodes.inputs import NODES, ROOT, RAW, CLOCK


def audit(out):
    forbidden = [RAW, CLOCK, INPUT_RUN / 'HOLDOUT_CONTINUOUS_INPUT.npz',
        ROOT / 'logs/c2_pose_reset_qmt_avatar_v16_20260831_215300/POSE_RESET_QMT_TRAJECTORY.npz',
        ROOT / 'logs/c2_hxx_frozen_replay_20260831_220900/HXX_FROZEN_C2_REPLAY_TRAJECTORY.npz']
    checks = []
    for path in forbidden:
        try:
            with path.open('rb') as stream:
                stream.read(1)
        except (FileNotFoundError, PermissionError):
            checks.append(dict(path=str(path), read_denied=True))
        else:
            raise RuntimeError('forbidden data are visible in computation namespace: ' + str(path))
    episodes = load_input(INPUT_RUN / 'CALIBRATION_CONTINUOUS_INPUT.npz')
    if {name[:2] for name in episodes if name != '_continuous'} != FIT:
        raise ValueError('all recorded calibration actions required')
    if any(set(nodes) != set(NODES) for nodes in episodes.values()):
        raise ValueError('five-node schema violated')
    write(out / 'DATA_BOUNDARY_AUDIT.json', dict(
        status='FIVE_NODE_INPUT_AND_FILESYSTEM_BOUNDARY_PASSED', denied_reads=checks,
        consumed_nodes=list(NODES), fit_actions=[name for name in episodes if name != '_continuous'],
        continuous_rows={node: len(episodes['_continuous'][node]['imu']) for node in NODES},
        pose_accuracy_accepted=False,
        qualification='tests exported-data and runtime isolation; original recording contained ten worn nodes'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    audit(parser.parse_args().out.resolve())
