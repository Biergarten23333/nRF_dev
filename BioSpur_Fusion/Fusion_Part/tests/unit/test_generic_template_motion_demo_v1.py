from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from biospur_fusion.visualization.generic_motion_demo_v1 import (
    DISCLAIMER, EDGES, LANDMARKS, LANDMARK_INDEX, NODE_ORDER,
    _length_errors, deterministic_display_frame, run_analysis,
)


ROOT=Path(__file__).resolve().parents[3]
CONFIG=ROOT/"Fusion_Part/config/generic_template_motion_demo_v1"


def load(name):return json.loads((CONFIG/name).read_text())


def test_generic_template_is_exact_symmetric_and_frozen():
    template=load("GENERIC_ADULT_PROXY_V1.json");d=template["dimensions"]
    assert template["template_name"]=="GENERIC_ADULT_PROXY_V1"
    assert template["frozen_before_payload_access"] is True
    assert d=={"graphical_shoulder_width_m":.4,"graphical_hip_width_m":.3,"C7Proxy_to_PelvisProxy_m":.5,
        "rendering_upper_arm_length_L_m":.32,"rendering_upper_arm_length_R_m":.32,
        "rendering_forearm_length_L_m":.26,"rendering_forearm_length_R_m":.26,
        "rendering_thigh_length_L_m":.43,"rendering_thigh_length_R_m":.43,
        "rendering_shank_length_L_m":.43,"rendering_shank_length_R_m":.43}
    assert hashlib.sha256((CONFIG/"GENERIC_ADULT_PROXY_V1.json").read_bytes()).hexdigest()=="892fa156db60f19120e45ea9bc537361e5ef4f73518d1753aa38fd73d389fce4"


def test_gates_seal_operator_and_both_heldout_payloads():
    gates=load("demo_gates_v1.json")
    assert gates["operator_measurements"]=="SEALED_AND_FORBIDDEN"
    assert gates["allowed_payload"]=="CALIBRATION_TYPED_LEDGER_ONLY"
    assert gates["heldout"]=={"walk":"SEALED","final_still":"SEALED"}
    assert "walk" not in gates["calibration_actions"] and "final_still" not in gates["calibration_actions"]


def test_failed_capture_derived_dimensions_are_not_imported_or_named():
    source=(ROOT/"Fusion_Part/src/biospur_fusion/visualization/generic_motion_demo_v1.py").read_text()
    assert "capture_derived_audit" not in source
    assert "analysis_capture_derived" not in source
    assert "0.691" not in source and "0.700" not in source and "0.800" not in source


def test_topology_is_connected_and_preserves_bilateral_names():
    adjacency={name:set() for name in LANDMARKS}
    for a,b in EDGES:adjacency[a].add(b);adjacency[b].add(a)
    seen={"Pelvis"};stack=["Pelvis"]
    while stack:
        node=stack.pop()
        for other in adjacency[node]-seen:seen.add(other);stack.append(other)
    assert seen==set(LANDMARKS)
    assert all(any(name.endswith(side) for name in LANDMARKS) for side in ("_L","_R"))


def test_generic_lengths_are_exact_by_construction_fixture():
    template=load("GENERIC_ADULT_PROXY_V1.json");s=np.zeros((3,len(LANDMARKS),3));i=LANDMARK_INDEX
    s[:,i["C7Proxy"],2]=.5;s[:,i["Shoulder_L"]]=[-.2,0,.5];s[:,i["Shoulder_R"]]=[.2,0,.5]
    s[:,i["Elbow_L"]]=[-.52,0,.5];s[:,i["Elbow_R"]]=[.52,0,.5];s[:,i["Wrist_L"]]=[-.78,0,.5];s[:,i["Wrist_R"]]=[.78,0,.5]
    s[:,i["Hip_L"]]=[-.15,0,0];s[:,i["Hip_R"]]=[.15,0,0];s[:,i["Knee_L"]]=[-.15,0,-.43];s[:,i["Knee_R"]]=[.15,0,-.43];s[:,i["Ankle_L"]]=[-.15,0,-.86];s[:,i["Ankle_R"]]=[.15,0,-.86]
    detail,maximum=_length_errors(s,template)
    assert maximum<1e-15 and all(v["maximum_absolute_error_m"]<1e-15 for v in detail.values())


def test_display_yaw_puts_dominant_root_motion_on_positive_x():
    raw=np.full((20,len(NODE_ORDER),3),np.nan);actions=np.asarray(["initial_still_attempt2"]*10+["t_pose"]*10)
    from biospur_fusion.visualization.generic_motion_demo_v1 import NODE_INDEX
    for k in range(20):
        pelvis=np.array([0.,k*.03,1.]);raw[k,NODE_INDEX["BSFC2CC"]]=pelvis;raw[k,NODE_INDEX["BSF31CC"]]=pelvis+[0,0,.5]
        raw[k,NODE_INDEX["BSF6C53"]]=pelvis+[-.1,0,-.9];raw[k,NODE_INDEX["BSF8BC4"]]=pelvis+[.1,0,-.9]
    rotation,audit=deterministic_display_frame(raw,actions)
    delta=rotation@(raw[-1,NODE_INDEX["BSFC2CC"]]-raw[0,NODE_INDEX["BSFC2CC"]])
    assert delta[0]>0 and abs(delta[1])<1e-10
    assert audit["compass_heading_claimed"] is False


def test_watermark_is_exact_required_language():
    assert DISCLAIMER=="Generic non-clinical motion demo. Skeleton dimensions are not subject-specific. Axial twist and clinical joint angles are not validated."


def test_heldout_path_is_rejected_before_output_creation(tmp_path):
    output=tmp_path/"out"
    with pytest.raises(ValueError,match="only calibration typed ledger"):
        run_analysis(tmp_path/"HELDOUT_TYPED_LEDGER.npz",tmp_path/"layout.json",CONFIG/"GENERIC_ADULT_PROXY_V1.json",CONFIG/"demo_gates_v1.json",output)
    assert not output.exists()
