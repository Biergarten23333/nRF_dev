"""Staged calibration, freeze, isolated replay, and gate evaluation."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from .common_time import build_common_timeline
from .core import (SEGMENTS,action_masks,continuous_replay,corrected_rotations,
                   derive_functional_parameters,evaluate_preview,
                   fit_preview_calibration,run_ablations)
from .io import dump_json,load_calibration_ledger,savez_deterministic,sha256
from .q2 import run_q2_frontend


def _load_inputs(gates_path:Path,template_path:Path):
    gates=json.loads(Path(gates_path).read_text());template=json.loads(Path(template_path).read_text())
    if sha256(Path(template_path))!=gates["template"]["sha256"]:raise ValueError("generic template SHA mismatch")
    return gates,template


def _source_audit() -> dict:
    root=Path(__file__).resolve().parents[4]
    paths=[
        root/"Fusion_Part/src/biospur_fusion/imu_preview_v0/q2.py",
        root/"Fusion_Part/src/biospur_fusion/imu_preview_v0/common_time.py",
        root/"Fusion_Part/src/biospur_fusion/imu_preview_v0/core.py",
        root/"Fusion_Part/src/biospur_fusion/imu_preview_v0/pipeline.py",
        root/"Fusion_Part/src/biospur_fusion/imu/q1.py",
        root/"Fusion_Part/src/biospur_fusion/visualization/renderer_v1.py",
        root/"Fusion_Part/config/generic_template_motion_demo_v1/GENERIC_ADULT_PROXY_V1.json",
    ]
    return {"schema":"biospur-phase-a-reuse-audit-v0","audited_local_phase_a":{"q2_frontend":{"path":"Fusion_Part/src/biospur_fusion/imu_mocap/q2_frontend.py","sha256":"1569bf381668ec6391ba78c74ad90aeaec0b498d24bff447bdc21b7ebef606b8"},"continuous_estimator":{"path":"Fusion_Part/src/biospur_fusion/imu_mocap/baseline_v1.py","sha256":"5921eba6413763c574a77ad8b4a76e922967818e647f5d799cc95ff4921e7ae4","reused":False,"reason":"nearest-neighbour cross-node association and label-triggered contact/root behavior violate V0"},"runner":{"path":"Fusion_Part/tools/run_imu_only_mocap_baseline_v1.py","sha256":"8d18d0f80f6373064b607641e193365932888338cea8e0a7aa862c7768b35d57"}},"minimum_runtime_closure":[{"path":str(p.relative_to(root)),"sha256":sha256(p)} for p in paths if p.exists()],"dependency_graph":{"pipeline.py":["io.py","q2.py","common_time.py","core.py"],"q2.py":["biospur_fusion/imu/q1.py"],"core.py":["numpy","scipy.optimize.least_squares","scipy.sparse","scipy.spatial.transform"],"renderer.py":["matplotlib","ffmpeg","Pillow"]}}


def _transverse_frames(timeline,windows,gates,calibration):
    rot,_=corrected_rotations(timeline,gates["node_to_segment"],calibration);masks=action_masks(timeline.time_ns,windows);node_to_segment=gates["node_to_segment"]
    for segment in ("pelvis","torso"):
        k=SEGMENTS.index(segment);rows=np.flatnonzero(masks["initial_still_attempt2"]&timeline.all_nodes_valid);long=np.asarray(calibration["segments"][segment]["board_frame_longitudinal_axis"]);mean=np.mean(rot[rows,k],axis=0);global_lat=np.array([1.,0,0]);local=mean.T@global_lat;local=local-long*float(local@long);local/=max(np.linalg.norm(local),1e-12);calibration["pelvis_torso_transverse_frame"][segment]={"board_frame_transverse_axis":local.tolist(),"provenance":"INITIAL_STILL_GRAVITY_FRAME_DISPLAY_LATERAL_GAUGE","absolute_heading_claimed":False}


def analyze_calibration(ledger_path:Path,template_path:Path,gates_path:Path,output:Path) -> dict:
    output=Path(output)
    if output.exists():raise ValueError("output directory already exists")
    output.mkdir(parents=True);gates,template=_load_inputs(gates_path,template_path);imus,windows,access=load_calibration_ledger(ledger_path,gates);q2,q2audit=run_q2_frontend(imus,windows,gates["q2"]);start=min(x[0] for x in windows.values());stop=max(x[1] for x in windows.values());timeline=build_common_timeline(q2,start,stop,gates["common_time"]);calibration,solver,_=fit_preview_calibration(timeline,windows,gates,template);_transverse_frames(timeline,windows,gates,calibration);derive_functional_parameters(timeline,windows,gates,calibration);arrays=continuous_replay(timeline,gates,calibration,template);action_use,preview_gates,boundary=evaluate_preview(arrays,timeline,windows,gates,template,solver,calibration);ablations=run_ablations(timeline,windows,gates,calibration,arrays);preview_gates["all_ablations_data_driven"]=all(row["pass"] for row in ablations.values());passed=all(preview_gates.values()) and q2audit["verdict"]=="PASS_ENGINEERING_FRONTEND"
    calibration.update({"gates_sha256":sha256(gates_path),"template_sha256":sha256(template_path),"calibration_ledger_sha256":sha256(ledger_path),"timeline_start_global_time_ns":int(start),"timeline_stop_global_time_ns":int(stop),"functional_parameter_labels_used_only_during_calibration":True,"replay_labels_allowed_only_posthoc":True})
    artifacts={"phase_a_audit":_source_audit(),"data_access":access,"q2":q2audit,"timestamp_accounting":timeline.accounting,"solver":solver,"action_use":action_use,"boundary":boundary,"ablations":ablations,"gates":preview_gates}
    for name,value in (("PHASE_A_REUSE_AUDIT.json",artifacts["phase_a_audit"]),("DATA_ACCESS_AUDIT.json",access),("Q2_FRONTEND_AUDIT.json",q2audit),("TIMESTAMP_OBSERVATION_ACCOUNTING.json",timeline.accounting),("SOLVER_AUDIT.json",solver),("ACTION_USE_SENSITIVITY_MATRIX.json",action_use),("BOUNDARY_RESET_AUDIT.json",boundary),("ABLATION_RESULTS.json",ablations),("CALIBRATION_PREVIEW_GATES.json",preview_gates)):dump_json(output/name,value)
    savez_deterministic(output/"CALIBRATION_PREVIEW_STATE.npz",arrays)
    verdict="PASS_IMU_RELATIVE_ORIENTATION_PREVIEW_V0" if passed else "FAIL_PREVIEW_CALIBRATION"
    if passed:
        frozen=output/"FROZEN_PREVIEW_CALIBRATION.json";dump_json(frozen,calibration);digest=sha256(frozen);(output/"FROZEN_PREVIEW_CALIBRATION.sha256").write_text(f"{digest}  FROZEN_PREVIEW_CALIBRATION.json\n")
    result={"schema":"biospur-imu-relative-orientation-preview-result-v0","verdict":verdict,"calibration_internal_gates_pass":passed,"preview_media_required_for_final_pass":True,"frozen_calibration_written":passed,"golf_swing":"SEALED_NOT_OPENED","boxing":"SEALED_NOT_OPENED","walk":"SEALED_NOT_OPENED","final_still":"SEALED_NOT_OPENED","uwb_t4_anchor_accessed":False,"operator_measurements_accessed":False,"s2_scientific_calibration_claimed":False,"failures":[name for name,value in preview_gates.items() if not value]}
    dump_json(output/"RESULT.json",result);(output/"REPORT.md").write_text("# IMU_RELATIVE_ORIENTATION_PREVIEW_V0 calibration report\n\n"+f"Verdict: `{verdict}`\n\nThis non-clinical preview uses real calibration IMU data on a strict global-time grid and a fixed generic skeleton. Root translation is a pelvis-origin display gauge. It does not claim S2 scientific calibration, absolute position, anatomical ground truth, or clinical joint angles.\n\nGolf, boxing, walk, and final_still were not opened in this stage.\n")
    return result


def replay_frozen(ledger_path:Path,template_path:Path,gates_path:Path,frozen_path:Path,frozen_sha_path:Path,output:Path) -> dict:
    expected=Path(frozen_sha_path).read_text().split()[0]
    if sha256(frozen_path)!=expected:raise ValueError("frozen calibration SHA mismatch")
    calibration=json.loads(Path(frozen_path).read_text());gates,template=_load_inputs(gates_path,template_path);imus,windows,access=load_calibration_ledger(ledger_path,gates)
    if sha256(ledger_path)!=calibration["calibration_ledger_sha256"] or sha256(gates_path)!=calibration["gates_sha256"]:raise ValueError("frozen input binding mismatch")
    q2,q2audit=run_q2_frontend(imus,windows,gates["q2"]);timeline=build_common_timeline(q2,int(calibration["timeline_start_global_time_ns"]),int(calibration["timeline_stop_global_time_ns"]),gates["common_time"]);arrays=continuous_replay(timeline,gates,calibration,template)
    output=Path(output)
    if output.exists():raise ValueError("replay output exists")
    output.mkdir(parents=True);savez_deterministic(output/"CONTINUOUS_STATE_TIMELINE.npz",arrays);labels=np.full(len(arrays["time_ns"]),"transition_unscored",dtype="U32")
    for name,(start,stop) in windows.items():labels[(arrays["time_ns"]>=start)&(arrays["time_ns"]<=stop)]=name
    np.save(output/"POSTHOC_ACTION_LABELS.npy",labels,allow_pickle=False);audit={"schema":"biospur-isolated-frozen-preview-replay-v0","loaded_calibration_absolute_path":str(Path(frozen_path).resolve()),"loaded_calibration_sha256":expected,"sha_verified_before_replay":True,"fit_object_available":False,"timeline":"ONE_CONTINUOUS_LABEL_BLIND_REPLAY","initialization_count":1,"pose_resets":0,"heading_resets":0,"extrinsic_resets":0,"root_resets":0,"velocity_resets":0,"ankle_reanchors":0,"labels_applied_after_state_replay":True,"state_sha256":sha256(output/"CONTINUOUS_STATE_TIMELINE.npz"),"walk":"SEALED_NOT_OPENED","final_still":"SEALED_NOT_OPENED","data_access":access,"q2_verdict":q2audit["verdict"]};dump_json(output/"REPLAY_AUDIT.json",audit);return audit


def render_calibration(*args,**kwargs):
    from .renderer import render_calibration as implementation
    return implementation(*args,**kwargs)
