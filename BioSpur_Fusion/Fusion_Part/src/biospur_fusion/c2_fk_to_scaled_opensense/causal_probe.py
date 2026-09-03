"""Seconds-scale closure probes for the sealed generic/scaled diagnostic."""

from __future__ import annotations

import json, math, time
from pathlib import Path
import numpy as np
import opensim as osim

from biospur_fusion.c2_3a_kinematics import load_frozen_c2_3a, load_frozen_c2_hxx_diagnostics
from biospur_fusion.c2_fk_to_opensim_ik.adapter import _quat_wxyz_matrix
from .pipeline import BODY_BY_SEGMENT, COUPLED_COORDINATES, FRAME_BY_SEGMENT

EDGES={"trunk":("pelvis","torso"),"shoulder_l":("torso","upper_arm_left"),"elbow_l":("upper_arm_left","forearm_left"),"shoulder_r":("torso","upper_arm_right"),"elbow_r":("upper_arm_right","forearm_right"),"hip_l":("pelvis","thigh_left"),"knee_l":("thigh_left","shank_left"),"hip_r":("pelvis","thigh_right"),"knee_r":("thigh_right","shank_right")}
C=np.array([[1.,0.,0.],[0.,0.,1.],[0.,-1.,0.]])

def mat(rotation): return np.array([[rotation.get(i,j) for j in range(3)] for i in range(3)])
def angle(a,b):
    return float(math.acos(np.clip((np.trace(a.T@b)-1.)/2.,-1.,1.)))
def state_from_row(model,state,table,row):
    cs=model.updCoordinateSet(); by={cs.get(i).getName():cs.get(i) for i in range(cs.getSize())}
    for name in table.getColumnLabels():
        c=by[name]
        if c.getLocked(state): continue
        value=float(np.asarray(table.getDependentColumn(name).to_numpy(),dtype=float)[row])
        if int(c.getMotionType())==1 or name in COUPLED_COORDINATES: value=math.radians(value)
        c.setValue(state,value,False)
    model.realizePosition(state)
def frame_rot(model,state,segment): return mat(model.getComponent(f"/bodyset/{BODY_BY_SEGMENT[segment]}/{FRAME_BY_SEGMENT[segment]}").getRotationInGround(state))

def main(workspace: Path, evidence: Path):
    started=time.monotonic(); out=evidence/"causal_closeout"; out.mkdir(parents=True,exist_ok=True)
    osim.Logger.removeFileSink(); osim.Logger.addFileSink(str(out/"opensim.log"))
    frozen=load_frozen_c2_3a(workspace=workspace); hxx=load_frozen_c2_hxx_diagnostics(workspace=workspace)
    configured=out.parent/"model/scaled_with_body_named_frames_attempt_002.osim"; calibrated=out.parent/"model/calibrated_attempt_002.osim"
    calibration=out.parent/"calibration/02_central_robust_mean_attempt_002.sto"
    qtable=osim.TimeSeriesTableQuaternion(str(calibration)); osim.OpenSenseUtilities.rotateOrientationTable(qtable,osim.Rotation(-math.pi/2,osim.Vec3(1,0,0)))
    rtable=osim.OpenSenseUtilities.convertQuaternionsToRotations(qtable)
    calibration_row=rtable.getRowAtIndex(0)
    target={name:mat(calibration_row.getElt(0,index)) for index,name in enumerate(rtable.getColumnLabels())}
    closure={}
    for tag,path in (("before",configured),("after",calibrated)):
        model=osim.Model(str(path)); state=model.initSystem(); model.assemble(state); model.realizePosition(state)
        closure[tag]={segment:angle(frame_rot(model,state,segment),target[FRAME_BY_SEGMENT[segment]]) for segment in BODY_BY_SEGMENT}
    model=osim.Model(str(calibrated)); state=model.initSystem(); motion02=osim.TimeSeriesTable(str(out.parent/"episodes_attempt_002_body_names/02_t_pose/official_ik.sto")); state_from_row(model,state,motion02,350)
    closure["ik_row_350"]={}
    ep=frozen.episodes["01"]
    for segment in BODY_BY_SEGMENT:
        measured=C@_quat_wxyz_matrix(ep.segments[segment].quat_world_segment_wxyz[350])
        closure["ik_row_350"][segment]=angle(frame_rot(model,state,segment),measured)

    samples={"01":[350],"10":[0,350,700],"H01_boxing":[0,300,600],"H02_golf":[0,300,599]}; relative={}
    for key,rows in samples.items():
        episode=hxx.episodes[key] if key.startswith("H") else frozen.episodes[key]
        label={"01":"02_t_pose","10":"10_lower_dynamic","H01_boxing":"H01_boxing","H02_golf":"H02_golf"}[key]
        motion=osim.TimeSeriesTable(str(out.parent/f"episodes_attempt_002_body_names/{label}/official_ik.sto")); relative[key]={}
        for row in rows:
            state=model.initSystem(); state_from_row(model,state,motion,row); values={}
            for edge,(parent,child) in EDGES.items():
                mp=C@_quat_wxyz_matrix(episode.segments[parent].quat_world_segment_wxyz[row]); mc=C@_quat_wxyz_matrix(episode.segments[child].quat_world_segment_wxyz[row])
                pp=frame_rot(model,state,parent); pc=frame_rot(model,state,child)
                values[edge]=angle(mp.T@mc,pp.T@pc)
            relative[key][str(row)]=values

    # One official IK call containing +/-0.1 rad single-coordinate witnesses.
    model=osim.Model(str(calibrated)); coords=model.updCoordinateSet(); by={coords.get(i).getName():coords.get(i) for i in range(coords.getSize())}
    tests=["pelvis_tilt","hip_flexion_r","knee_angle_r","lumbar_extension","arm_flex_r","elbow_flex_r"]
    labels=[FRAME_BY_SEGMENT[s] for s in BODY_BY_SEGMENT]; truth=[]; lines=[]
    for index,(name,value) in enumerate((item for name in tests for item in ((name,0.1),(name,-0.1)))):
        state=model.initSystem(); by={coords.get(i).getName():coords.get(i) for i in range(coords.getSize())}; by[name].setValue(state,value,False); model.assemble(state); model.realizePosition(state)
        actual=float(by[name].getValue(state)); truth.append((name,value,actual))
        qs=[]
        for segment in BODY_BY_SEGMENT:
            q=model.getComponent(f"/bodyset/{BODY_BY_SEGMENT[segment]}/{FRAME_BY_SEGMENT[segment]}").getRotationInGround(state).convertRotationToQuaternion(); qs.append(",".join(f"{q.get(i):.17g}" for i in range(4)))
        lines.append(f"{index*.01:.2f}\t"+"\t".join(qs))
    sto=out/"single_dof_truth.sto"; sto.write_text("DataRate=100\nDataType=Quaternion\nversion=3\nOpenSimVersion=4.6\nendheader\ntime\t"+"\t".join(labels)+"\n"+"\n".join(lines)+"\n")
    tool=osim.IMUInverseKinematicsTool(); tool.set_model_file(str(calibrated)); tool.set_orientations_file(str(sto)); tool.set_sensor_to_opensim_rotations(osim.Vec3(0,0,0)); tool.set_time_range(0,0); tool.set_time_range(1,.11); tool.set_results_directory(str(out)); tool.set_output_motion_file("single_dof_ik.sto"); tool.set_report_errors(True); tool.set_accuracy(1e-7)
    if not tool.run(False): raise RuntimeError("single-DoF official IK failed")
    table=osim.TimeSeriesTable(str(out/"single_dof_ik.sto")); unit=[]
    for row,(name,requested,assembled) in enumerate(truth):
        degree=float(np.asarray(table.getDependentColumn(name).to_numpy(),dtype=float)[row]); unit.append({"coordinate":name,"requested_rad":requested,"assembled_truth_rad":assembled,"official_sto_degree":degree,"official_sto_as_rad":math.radians(degree),"error_to_assembled_rad":abs(math.radians(degree)-assembled),"clamped":bool(by[name].getDefaultClamped()),"range_rad":[float(by[name].getRangeMin()),float(by[name].getRangeMax())]})
    result={"wall_s":time.monotonic()-started,"placement_closure_rad":closure,"relative_frame_mismatch_rad":relative,"single_dof_round_trip":unit,"no_model_or_placement_change":True}
    (out/"CAUSAL_CLOSURE.json").write_text(json.dumps(result,indent=2,sort_keys=True)+"\n"); print(json.dumps(result,indent=2,sort_keys=True))

if __name__=="__main__":
    import argparse
    p=argparse.ArgumentParser(); p.add_argument("--workspace",type=Path,required=True); p.add_argument("--evidence",type=Path,required=True); a=p.parse_args(); main(a.workspace,a.evidence)
