#!/usr/bin/env python3
"""Run H with immutable C2 parameters and an explicit diagnostic scope."""
import argparse
import json
from pathlib import Path

import torch
from biospur_fusion.c2_five_calibration.holdout import replay


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--diagnostic-only', action='store_true')
    parser.add_argument('--reuse-neural-from', type=Path)
    parser.add_argument('--calibration-kind', choices=('legacy', 'shared'), default='legacy')
    parser.add_argument('--replay-contract', type=Path)
    args = parser.parse_args()
    compatibility = None
    if args.calibration_kind == 'shared':
        path = args.replay_contract or args.out / 'SHARED_REPLAY_CONTRACT.json'
        if path.resolve() != (args.out / 'SHARED_REPLAY_CONTRACT.json').resolve():
            parser.error('shared replay contract must be inside this candidate output')
        compatibility = json.loads(path.read_text())['compatibility']
    elif args.replay_contract is not None:
        parser.error('--replay-contract requires shared calibration')
    torch.set_num_threads(2)
    replay(args.out, diagnostic_only=args.diagnostic_only, neural_source=args.reuse_neural_from,
           calibration_kind=args.calibration_kind, compatibility=compatibility)
