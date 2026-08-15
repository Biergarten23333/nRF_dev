import json
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

from biospur_fusion.imu_multi_action_engineering_v1.relative_excitation import relative_excitation

ROOT=Path(__file__).resolve().parents[3]
CONTRACT=json.loads((ROOT/"Fusion_Part/config/imu_multi_action_engineering_preview_v1/ACTION_RELATIVE_EXCITATION_CONTRACT.json").read_text())


def _trajectory(degrees,axis="y"):
    vector={"x":[1,0,0],"y":[0,1,0],"z":[0,0,1]}[axis];return Rotation.from_rotvec(np.deg2rad(np.asarray(degrees))[:,None]*np.asarray(vector)[None]).as_matrix()


def _cov(n,sigma_deg=1.):return np.tile(np.eye(3)*np.deg2rad(sigma_deg)**2,(n,1,1))


def test_isolated_elbow_curl_passes_when_upper_arm_is_stable():
    angle=np.r_[np.linspace(0,70,50),np.linspace(70,0,50)];parent=np.tile(np.eye(3),(len(angle),1,1));child=_trajectory(angle);result=relative_excitation(parent,child,_cov(len(angle)),_cov(len(angle)),CONTRACT)
    assert result["pass"] and result["proximal_step_rms_rad"]<1e-12


def test_trunk_rotation_passes_when_pelvis_is_stable():
    angle=45*np.sin(np.linspace(0,4*np.pi,120));pelvis=np.tile(np.eye(3),(len(angle),1,1));torso=_trajectory(angle,"z");result=relative_excitation(pelvis,torso,_cov(len(angle)),_cov(len(angle)),CONTRACT)
    assert result["pass"] and result["status"]=="PASS_RELATIVE_EXCITATION"


def test_only_proximal_motion_without_relative_joint_excitation_fails():
    angle=60*np.sin(np.linspace(0,2*np.pi,100));parent=_trajectory(angle);child=parent.copy();result=relative_excitation(parent,child,_cov(len(angle)),_cov(len(angle)),CONTRACT)
    assert not result["pass"] and result["status"]=="FAIL_NO_RELATIVE_JOINT_EXCITATION"
