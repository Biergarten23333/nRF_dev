#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"Fusion_Part/src"))

from biospur_fusion.imu_multi_action_engineering_v1 import run_phase_a
from biospur_fusion.imu_multi_action_engineering_v1.pipeline import analyze,replay


def main()->int:
    parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest="command",required=True);phase=sub.add_parser("phase-a")
    for name in ("ledger","gates","output"):phase.add_argument(f"--{name}",type=Path,required=True)
    calibration=sub.add_parser("calibrate")
    for name in ("phase-a-dir","template","gates","contract","output"):calibration.add_argument(f"--{name}",type=Path,required=True)
    calibration.add_argument("--resume-checkpoint",type=Path)
    calibration.add_argument("--resume-parameterization",choices=("SPHERICAL_AZIMUTH_ELEVATION_WITH_POLE_SINGULARITY","REFERENCE_CENTERED_S2_TANGENT_CHART"))
    frozen=sub.add_parser("replay")
    for name in ("cache","phase-result","template","gates","frozen","frozen-sha","output"):frozen.add_argument(f"--{name}",type=Path,required=True)
    args=parser.parse_args()
    if args.command=="phase-a":result=run_phase_a(args.ledger,args.gates,args.output)
    elif args.command=="calibrate":result=analyze(args.phase_a_dir,args.template,args.gates,args.contract,args.output,args.resume_checkpoint,args.resume_parameterization)
    else:result=replay(args.cache,args.phase_result,args.template,args.gates,args.frozen,args.frozen_sha,args.output)
    print(json.dumps(result,sort_keys=True));return 0 if result["pass"] else 2


if __name__=="__main__":raise SystemExit(main())
