#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"Fusion_Part/src"))

from biospur_fusion.imu_preview_v0.pipeline import analyze_calibration,replay_frozen,render_calibration
from biospur_fusion.imu_preview_v0.revision_c import run_real_revision_c,run_synthetic_qualification


def main() -> int:
    parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest="command",required=True)
    analyze=sub.add_parser("analyze-calibration")
    for name in ("ledger","template","gates","output"):analyze.add_argument(f"--{name}",type=Path,required=True)
    replay=sub.add_parser("replay-isolated")
    for name in ("ledger","template","gates","frozen","frozen-sha","output"):replay.add_argument(f"--{name}",type=Path,required=True)
    render=sub.add_parser("render-calibration")
    for name in ("replay","analysis","gates","output"):render.add_argument(f"--{name}",type=Path,required=True)
    qualify=sub.add_parser("qualify-calibration")
    for name in ("ledger","template","gates","output"):qualify.add_argument(f"--{name}",type=Path,required=True)
    synthetic_c=sub.add_parser("qualify-revision-c-synthetic")
    for name in ("gates","output"):synthetic_c.add_argument(f"--{name}",type=Path,required=True)
    real_c=sub.add_parser("run-revision-c-calibration")
    for name in ("ledger","template","gates","output"):real_c.add_argument(f"--{name}",type=Path,required=True)
    args=parser.parse_args()
    if args.command=="analyze-calibration":result=analyze_calibration(args.ledger,args.template,args.gates,args.output)
    elif args.command=="replay-isolated":result=replay_frozen(args.ledger,args.template,args.gates,args.frozen,args.frozen_sha,args.output)
    elif args.command=="render-calibration":result=render_calibration(args.replay,args.analysis,args.gates,args.output)
    elif args.command=="qualify-calibration":
        analysis=args.output/"analysis";replay_dir=args.output/"isolated_replay";media=args.output/"calibration_media"
        result=analyze_calibration(args.ledger,args.template,args.gates,analysis)
        if not result["calibration_internal_gates_pass"]:
            print(json.dumps(result));return 2
        command=[sys.executable,str(Path(__file__).resolve()),"replay-isolated","--ledger",str(args.ledger),"--template",str(args.template),"--gates",str(args.gates),"--frozen",str(analysis/"FROZEN_PREVIEW_CALIBRATION.json"),"--frozen-sha",str(analysis/"FROZEN_PREVIEW_CALIBRATION.sha256"),"--output",str(replay_dir)]
        completed=subprocess.run(command,check=False)
        if completed.returncode:raise RuntimeError(f"isolated replay failed: {completed.returncode}")
        result=render_calibration(replay_dir,analysis,args.gates,media)
    elif args.command=="qualify-revision-c-synthetic":
        result=run_synthetic_qualification(json.loads(args.gates.read_text()),args.output)
    else:
        source_paths=[
            Path(__file__).resolve(),
            ROOT/"Fusion_Part/src/biospur_fusion/imu_preview_v0/revision_c.py",
            ROOT/"Fusion_Part/src/biospur_fusion/imu_preview_v0/io.py",
            ROOT/"Fusion_Part/src/biospur_fusion/imu_preview_v0/q2.py",
            ROOT/"Fusion_Part/src/biospur_fusion/imu_preview_v0/common_time.py",
            ROOT/"Fusion_Part/src/biospur_fusion/imu_preview_v0/core.py",
            ROOT/"Fusion_Part/src/biospur_fusion/imu/q1.py",
            args.gates.resolve(),
            ROOT/"Fusion_Part/config/imu_only_multi_action_centerline_calibration_v1_s2/ACTION_SEMANTICS_MANIFEST.json",
            ROOT/"Fusion_Part/config/imu_only_multi_action_centerline_calibration_v1_s2/FRAME_AND_GAUGE_CONTRACT.json",
            ROOT/"Fusion_Part/config/imu_only_multi_action_centerline_calibration_v1_s2/OPERATOR_ACTION_CONTRACT.json",
        ]
        result=run_real_revision_c(args.ledger,args.template,args.gates,args.output,source_paths)
    print(json.dumps(result,sort_keys=True));return 0 if result.get("verdict")=="PASS_IMU_RELATIVE_ORIENTATION_PREVIEW_V0" or result.get("pass") is True or args.command=="replay-isolated" else 2


if __name__=="__main__":raise SystemExit(main())
