#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"Fusion_Part/src"))

from biospur_fusion.imu_mocap.baseline_v1 import render_pose_reference_diagnostics,render_previews,run_analysis


def main() -> int:
    parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest="command",required=True)
    analyze=sub.add_parser("analyze")
    for name in ("ledger","template","gates","output"):analyze.add_argument(f"--{name}",type=Path,required=True)
    render=sub.add_parser("render");render.add_argument("--analysis",type=Path,required=True);render.add_argument("--gates",type=Path,required=True)
    pose_render=sub.add_parser("render-pose-reference");pose_render.add_argument("--analysis",type=Path,required=True);pose_render.add_argument("--gates",type=Path,required=True);pose_render.add_argument("--ledger",type=Path,required=True)
    args=parser.parse_args()
    if args.command=="analyze":result=run_analysis(args.ledger,args.template,args.gates,args.output)
    elif args.command=="render":result=render_previews(args.analysis,args.gates)
    else:result=render_pose_reference_diagnostics(args.analysis,args.gates,args.ledger)
    print(json.dumps({"verdict":result["verdict"],"walk":"SEALED","final_still":"SEALED","committed":False}))
    return 0


if __name__=="__main__":raise SystemExit(main())
