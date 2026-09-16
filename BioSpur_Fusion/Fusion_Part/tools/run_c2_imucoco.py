#!/usr/bin/env python3
"""Gated published-model reproduction; run with PYTHONPATH=src .venv-v0/bin/python."""
import argparse
from pathlib import Path
import torch

from biospur_fusion.c2_sparse_nodes.inputs import ROOT
from biospur_fusion.c2_imucoco.workflow import prepare, encoder_probe, replay


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--stage', required=True, choices=['prepare', 'encoder', 'probe', 'replay'])
    parser.add_argument('--smpl', type=Path)
    parser.add_argument('--diagnostic-only', action='store_true',
        help='run an explicitly unaccepted baseline; current five-node calibration is incomplete')
    args = parser.parse_args()
    out = args.output.resolve()
    if not out.is_relative_to(ROOT/'logs'):
        parser.error('output must be under Fusion_Part/logs')
    out.mkdir(parents=True, exist_ok=True)
    torch.set_num_threads(2)
    torch.manual_seed(42)
    if args.stage == 'prepare':
        prepare(out)
    elif args.stage == 'encoder':
        encoder_probe(out)
    else:
        if args.smpl is None or not args.smpl.is_file():
            parser.error('--smpl must name an existing official SMPL male .pkl')
        replay(out, args.smpl.resolve(), probe=args.stage == 'probe', diagnostic_only=args.diagnostic_only)


if __name__ == '__main__':
    main()
