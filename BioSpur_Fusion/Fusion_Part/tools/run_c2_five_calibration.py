#!/usr/bin/env python3
"""Bounded C2-only five-node calibration stages; no H input path."""
import argparse
from pathlib import Path
import torch
from biospur_fusion.c2_five_calibration.workflow import prior, physical

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('stage',choices=('probe','prior','calibrate','validate'))
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    torch.set_num_threads(2)
    out=args.out.resolve()
    if not (out/'TASK_CONTRACT.json').is_file():
        parser.error('run requires predeclared TASK_CONTRACT.json')
    if args.stage in ('probe','prior'):
        prior(out,probe=args.stage=='probe')
    else:
        physical(out,validate=args.stage=='validate')
