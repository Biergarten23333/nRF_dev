#!/usr/bin/env python3
from __future__ import annotations

import argparse,json,sys
from pathlib import Path
import numpy as np
from scipy.spatial.transform import Rotation

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/"Fusion_Part/src"))
from biospur_fusion.imu_multi_action_engineering_v1.relative_excitation import relative_excitation


def trajectory(degrees,axis):return Rotation.from_rotvec(np.deg2rad(np.asarray(degrees))[:,None]*np.asarray(axis)[None]).as_matrix()
def covariance(n):return np.tile(np.eye(3)*np.deg2rad(1.)**2,(n,1,1))
def absolute_range(rotation):
    ref=Rotation.from_matrix(rotation).mean().as_matrix();return float(np.degrees(np.percentile(Rotation.from_matrix(np.einsum("ji,njk->nik",ref,rotation)).magnitude(),95)))


def main()->int:
    ap=argparse.ArgumentParser();ap.add_argument("--contract",type=Path,required=True);ap.add_argument("--output",type=Path,required=True);a=ap.parse_args();contract=json.loads(a.contract.read_text());cases=[]
    specs=[("isolated_elbow_curl_stable_upper_arm",np.zeros(100),np.r_[np.linspace(0,70,50),np.linspace(70,0,50)],[0,1,0],"PASS"),("trunk_rotation_stable_pelvis",np.zeros(120),45*np.sin(np.linspace(0,4*np.pi,120)),[0,0,1],"PASS"),("rigid_chain_proximal_motion_no_joint_excitation",60*np.sin(np.linspace(0,2*np.pi,100)),60*np.sin(np.linspace(0,2*np.pi,100)),[0,1,0],"FAIL")]
    for name,pangle,cangle,axis,expected in specs:
        parent=trajectory(pangle,axis);child=trajectory(cangle,axis);new=relative_excitation(parent,child,covariance(len(parent)),covariance(len(parent)),contract);pr=absolute_range(parent);cr=absolute_range(child);old_pass=min(pr,cr)>=8.;cases.append({"case":name,"expected_physical_result":expected,"old_absolute_minimum_gate":{"proximal_range_deg":pr,"distal_range_deg":cr,"minimum_range_deg":min(pr,cr),"threshold_deg":8.,"pass":old_pass},"new_relative_covariance_conditioned_gate":new,"counterexample_demonstrated":old_pass!=new["pass"]})
    result={"schema":"biospur-old-new-action-gate-counterexamples-v1","contract_path":str(a.contract),"cases":cases,"all_counterexamples_demonstrated":all(x["counterexample_demonstrated"] for x in cases)};a.output.write_text(json.dumps(result,sort_keys=True,separators=(",",":"))+"\n");return 0 if result["all_counterexamples_demonstrated"] else 2


if __name__=="__main__":raise SystemExit(main())
