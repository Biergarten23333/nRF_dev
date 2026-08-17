import json
import numpy as np

from biospur_fusion.imu_pose_v1.fk import normalized_fk,bone_lengths
from biospur_fusion.imu_pose_v1.serialization import frame_dict
from biospur_fusion.imu_pose_v1.visualization import render_triptych
from biospur_fusion.imu_pose_v1.types import PoseFrame,SEGMENTS


def make_frame(t=0):
    q={s:np.array([1.,0,0,0]) for s in SEGMENTS};p=normalized_fk(q)
    return PoseFrame(t,t,q,{},p,{s:.01 for s in SEGMENTS},{},{s:'USABLE_BODY_RELATIVE_TILT' for s in SEGMENTS},{},True,())


def test_fixed_bone_fk_and_machine_serialization():
    a=make_frame();b=make_frame(1);np.testing.assert_array_equal(bone_lengths(a.normalized_joint_positions),bone_lengths(b.normalized_joint_positions))
    d=frame_dict(a);json.dumps(d);assert d['root_world_position']=='UNAVAILABLE' and d['scale']=='MODEL_INFERRED_SCALE_CONDITIONAL'
    assert d['head']=='MODEL_INFERRED' and d['feet']=='UNAVAILABLE' and d['external_accuracy_claim'] is False


def test_triptych_animation_constructs(tmp_path):
    frames=[make_frame(i*.05) for i in range(4)];pos=tuple(f.normalized_joint_positions for f in frames)
    result=render_triptych(tmp_path/'x.gif',np.arange(4)*.05,{'B0':pos,'B1':pos,'P':pos},{k:('OK',)*4 for k in ('B0','B1','P')},fps=5,max_frames=4)
    assert (tmp_path/'x.gif').stat().st_size>0 and result['frames']==4 and result['fixed_normalized_geometry']
