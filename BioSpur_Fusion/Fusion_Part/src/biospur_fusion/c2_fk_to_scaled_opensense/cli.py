"""Bounded command line for the official generic/scaled pilot."""

from __future__ import annotations

import argparse, json
from pathlib import Path

import numpy as np

from biospur_fusion.c2_3a_kinematics import load_frozen_c2_3a, load_frozen_c2_hxx_diagnostics
from .pipeline import PILOT_EPISODES, configure_scaled_model, replay_model, run_identity, run_ik, run_imu_placer, write_calibration_table
from .render import a_points, render_episode

def main() -> int:
    p=argparse.ArgumentParser(); p.add_argument("command",choices=("prepare","identity","episode","render","full_episode")); p.add_argument("--workspace",type=Path,required=True); p.add_argument("--evidence",type=Path,required=True); p.add_argument("--label")
    a=p.parse_args(); frozen=load_frozen_c2_3a(workspace=a.workspace); model=a.evidence/"model/calibrated_attempt_002.osim"
    if a.command=="prepare":
        source=a.workspace/"logs/c2_3b_official_opensense_20260902_094743/upstream/official_example_original/Models/Rajagopal_OpenSense/Rajagopal2015_opensense.osim"
        configured=a.evidence/"model/scaled_with_body_named_frames_attempt_002.osim"
        result={"scale":configure_scaled_model(source,configured)}
        cal=a.evidence/"calibration/02_central_robust_mean_attempt_002.sto"; result["calibration_table"]=write_calibration_table(frozen.episodes["01"],cal); result["imu_placer"]=run_imu_placer(configured,cal,model)
        (a.evidence/"PREPARE_RESULT_ATTEMPT_002.json").write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
    elif a.command=="identity": result=run_identity(model,a.evidence/"identity_attempt_004_body_names")
    else:
        if a.command=="full_episode":
            if a.label not in frozen.episodes: raise ValueError("unknown primary episode")
            key=a.label; hxx=None; episode=frozen.episodes[key]; root=a.evidence/"all_19plus2"/f"primary_{key}"
        else:
            if a.label not in PILOT_EPISODES: raise ValueError("unknown label")
            key=PILOT_EPISODES[a.label]; hxx=load_frozen_c2_hxx_diagnostics(workspace=a.workspace) if key.startswith("H") else None; episode=hxx.episodes[key] if hxx else frozen.episodes[key]
            root=a.evidence/"episodes_attempt_002_body_names"/a.label
        if a.command in ("episode","full_episode"):
            result=run_ik(model,episode,root); (root/"EPISODE_RESULT.json").write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
        else:
            _,b,rom=replay_model(model,root/"official_ik.sto",root/"analysis_opensim.log")
            rendered=render_episode(frozen,episode,key,a.label,b,a.evidence/"rendering"/f"{a.label}_ab_front_side_top.png")
            changes=[]
            for i,row in enumerate(b):
                pa=a_points(episode,frozen.geometry,i)
                changes.extend(float(np.linalg.norm(pa[name]-row[name])) for name in pa)
            result={"render":rendered,"rom":rom,"point_change_m":{"mean":float(np.mean(changes)),"p95":float(np.quantile(changes,.95)),"max":float(np.max(changes))}}
            (root/"ANALYSIS_RESULT.json").write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
    print(json.dumps(result,indent=2,sort_keys=True)); return 0

if __name__=="__main__": raise SystemExit(main())
