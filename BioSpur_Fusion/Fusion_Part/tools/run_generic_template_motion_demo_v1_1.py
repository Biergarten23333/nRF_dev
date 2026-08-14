#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/"Fusion_Part/src"))
from biospur_fusion.visualization.generic_motion_demo_v1_1 import render_previews,run_analysis
def main():
 p=argparse.ArgumentParser();s=p.add_subparsers(dest="cmd",required=True);a=s.add_parser("analyze");
 for n in ("calibration-ledger","layout","template","gates","action-segments","custom-t4","custom-q1","output"):a.add_argument("--"+n,type=Path,required=True)
 r=s.add_parser("render");r.add_argument("--analysis",type=Path,required=True);r.add_argument("--gates",type=Path,required=True);r.add_argument("--output",type=Path,required=True);x=p.parse_args()
 result=run_analysis(x.calibration_ledger,x.layout,x.template,x.gates,x.action_segments,x.custom_t4,x.custom_q1,x.output) if x.cmd=="analyze" else render_previews(x.analysis,x.gates,x.output);print(json.dumps({"verdict":result.get("analysis_verdict",result.get("verdict")),"walk":"SEALED","final_still":"SEALED"}));return 0
if __name__=="__main__":raise SystemExit(main())
