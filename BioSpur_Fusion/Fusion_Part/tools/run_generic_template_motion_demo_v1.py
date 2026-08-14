#!/usr/bin/env python3
"""Run or render the sealed calibration-only generic motion demo."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Fusion_Part/src"))

from biospur_fusion.visualization.generic_motion_demo_v1 import render_preview, run_analysis


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest="command",required=True)
    analyze=sub.add_parser("analyze");analyze.add_argument("--calibration-ledger",type=Path,required=True);analyze.add_argument("--layout",type=Path,required=True);analyze.add_argument("--template",type=Path,required=True);analyze.add_argument("--gates",type=Path,required=True);analyze.add_argument("--output",type=Path,required=True)
    render=sub.add_parser("render");render.add_argument("--analysis",type=Path,required=True);render.add_argument("--gates",type=Path,required=True);render.add_argument("--mp4",type=Path,required=True);render.add_argument("--gif",type=Path,required=True)
    args=parser.parse_args()
    if args.command=="analyze":result=run_analysis(args.calibration_ledger,args.layout,args.template,args.gates,args.output)
    else:result=render_preview(args.analysis,args.gates,args.mp4,args.gif)
    print(json.dumps({"verdict":result.get("verdict","RENDER_COMPLETE"),"walk":"SEALED_NOT_OPENED","final_still":"SEALED_NOT_OPENED"},sort_keys=True));return 0


if __name__=="__main__":raise SystemExit(main())
