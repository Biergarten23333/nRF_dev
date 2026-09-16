#!/usr/bin/env python3
"""Run one bounded, isolated arm-reference diagnostic stage."""
import argparse
from pathlib import Path
import torch
from biospur_fusion.c2_five_calibration.arm_diagnostics import probe,profile

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('stage',choices=['probe','profile'])
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();torch.set_num_threads(1)
    (probe if args.stage=='probe' else profile)(args.out.resolve())
