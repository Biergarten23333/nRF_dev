import importlib.util,json,math
from pathlib import Path

TOOL=Path(__file__).resolve().parents[3]/'tools/fusion_v2/phase3r/phase3r_rescale_evidence_covariance.py'
spec=importlib.util.spec_from_file_location('migration',TOOL);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)

def test_covariance_only_migration_changes_no_pose_fields():
    row={'segment_tilt_sigma_rad':{'pelvis':.2},'joint_relative_sigma_rad':{'hip_left':.3},'segment_quality':{},'joint_quality':{},'whole_body_available':False,'degraded_reasons':['old'],'segment_quaternion_W_S':{'pelvis':[1,0,0,0]},'normalized_joint_position_L0':{'root':[0,0,0]}}
    before=json.loads(json.dumps(row));out=mod.migrate_frame(row)
    assert out['segment_quaternion_W_S']==before['segment_quaternion_W_S'] and out['normalized_joint_position_L0']==before['normalized_joint_position_L0']
    assert math.isclose(out['segment_tilt_sigma_rad']['pelvis'],before['segment_tilt_sigma_rad']['pelvis']*math.sqrt(.15))
