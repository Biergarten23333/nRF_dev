#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,sys
from pathlib import Path

REPO=Path(__file__).resolve().parents[5];BASE=REPO/'BioSpur_Fusion/Fusion_Part';sys.path.insert(0,str(BASE/'src'))
from biospur_fusion.imu_pose_v1.real_runner import RealSessionRunner,write_json,sha

ANIMATE={'00_initial_still','02_t_pose','04_shoulder_left','05_shoulder_right','06_elbow_left','07_elbow_right',
 '08_hip_left','09_hip_right','10_knee_left_seated','11_knee_right_seated','18_heel_to_butt_left',
 '14_trunk_flex_extend','15_trunk_axial_rotation','16_squat','H00_walk','H01_boxing','H02_golf'}

def main():
 p=argparse.ArgumentParser();p.add_argument('--dataset',type=Path,required=True);p.add_argument('--state',type=Path,required=True);p.add_argument('--evidence',type=Path,required=True);p.add_argument('--resume',action='store_true');a=p.parse_args()
 runner=RealSessionRunner(REPO,a.dataset,a.state,a.evidence);cal,axes,targets,axis_confidence,heading_confidence,calrep=runner.calibrate();summaries=[]
 order=[x['action_id'] for x in runner.selection['development_windows']]+[x['action_id'] for x in runner.selection['retrospective_diagnostics']]
 completed=set()
 if a.resume and (a.state/'PHASE3R_EXECUTION_CHECKPOINT.json').exists():
  checkpoint=json.loads((a.state/'PHASE3R_EXECUTION_CHECKPOINT.json').read_text());last=checkpoint.get('last_completed_action')
  if last in order:completed=set(order[:order.index(last)+1])
 for number,action in enumerate(order,1):
  summary_path=a.evidence/'actions'/action/'SUMMARY.json'
  if action in completed and summary_path.exists():summary=json.loads(summary_path.read_text())
  else:summary=runner.process(action,cal,axes,targets,axis_confidence,heading_confidence,action in ANIMATE)
  summaries.append(summary);print(f'P3R_REAL_PROGRESS {number}/{len(order)} {action}',flush=True)
  checkpoint=json.loads((a.state/'PHASE3R_EXECUTION_CHECKPOINT.json').read_text());checkpoint.update(status=f'P3R-REAL-{number:02d}-OF-{len(order):02d}',checkpoint_sequence=2+number,last_completed_action=action,uwb_numeric_decode=0)
  write_json(a.state/'PHASE3R_EXECUTION_CHECKPOINT.json',checkpoint)
 write_json(a.evidence/'REAL_ACTION_MASTER_SUMMARY.json',{'schema':'biospur-phase3r-real-action-master-v1','actions':summaries,
  'development_count':19,'retrospective_count':3,'retrospective_classification':'CONTAMINATED_RETROSPECTIVE_DIAGNOSTIC',
  'uwb_numeric_decode':0,'external_truth':False,'accuracy_claim':False})
 write_json(a.state/'DATA_ACCESS_SUMMARY_REAL.json',runner.broker.summary())
 write_json(a.evidence/'EVIDENCE_MANIFEST.json',{'schema':'biospur-phase3r-evidence-manifest-v1','calibration_sha256':sha(a.evidence/'REAL_SENSOR_TO_SEGMENT_AND_QMT_CALIBRATION.json'),
  'master_summary_sha256':sha(a.evidence/'REAL_ACTION_MASTER_SUMMARY.json'),'action_summary_sha256':{x['action_id']:sha(a.evidence/'actions'/x['action_id']/'SUMMARY.json') for x in summaries}})
 return 0
if __name__=='__main__':raise SystemExit(main())
