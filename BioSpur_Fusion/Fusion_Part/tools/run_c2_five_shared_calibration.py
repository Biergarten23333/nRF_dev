#!/usr/bin/env python3
"""Separate bounded probe/fit stages for five-C2 shared orientation proposals."""
import argparse
from pathlib import Path

import torch

from biospur_fusion.c2_five_calibration.shared_workflow import probe,fit


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('stage',choices=('probe','fit'))
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    out=args.out.resolve()
    if not (out/'TASK_CONTRACT.json').is_file():
        parser.error('run requires a predeclared TASK_CONTRACT.json in the output directory')
    torch.set_num_threads(1)
    (probe if args.stage=='probe' else fit)(out)
