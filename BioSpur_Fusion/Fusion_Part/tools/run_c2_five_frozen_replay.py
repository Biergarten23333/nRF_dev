#!/usr/bin/env python3
"""Explicit diagnostic-only shared-candidate C2 frozen replay."""
import argparse
from pathlib import Path

import torch
from biospur_fusion.c2_five_calibration.frozen_replay import replay


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--out',type=Path,required=True)
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--probe',action='store_true')
    mode.add_argument('--resource-probe',action='store_true')
    args=parser.parse_args()
    torch.set_num_threads(2)
    replay(args.out,probe=args.probe,resource_probe=args.resource_probe)
